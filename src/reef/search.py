"""Search across the caller's pages and files, under RLS.

Two rankings, merged. Postgres full-text search finds pages and files by
their words. Where the deployment has an embedding service, the stored
vectors find pages by their meaning, and the two rankings are fused. See
:func:`search_pages`. Without a service, or when it does not answer, search
is keyword search and nothing else changes.

Every query runs in the same armed transaction as every other read, so the
row-level policies scope it — a search can only ever rank content the
caller could have read anyway, and a forgotten filter returns nothing
rather than somebody else's memories.

The searchable text of a row is stored, not recomputed. Each table carries
a generated ``search_vector`` column, declared by :func:`search_statements`.
Before that, every search parsed every visible body, which was nearly all
of what a search cost.

There is deliberately **no GIN index** on that column. Postgres will not use
one here: a condition from the query may only become an index condition on a
row-secured table if its operator is marked leakproof, and the text match
operator ``@@`` is not. Measured on Postgres 18, the same query uses the
index as a superuser and falls back to a sequential scan as the app role.
The index would cost every write and serve no read. Marking the operator
leakproof needs superuser and weakens the guarantee that a query touches
only rows its caller may read, so it is not done.

Two text search configurations are indexed side by side, English and Dutch,
because reef's pages are written in both and a page does not say which.
Stemming is per language: ``contracten`` reaches ``contract`` only through
the Dutch stemmer. A query is parsed under both configurations and the two
are ORed, so either reading of a word can match.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

from reef import embedding, searchlog, sections
from reef import rerank as reranking
from reef.access import Principal, alias_map, arm, resolve_cove
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.indexfields import summary
from reef.models import AttachmentStatus, Page

log = logging.getLogger(__name__)

#: Text search configurations indexed for every row, in this order. Adding
#: one changes the stored column's expression, which a migration has to do.
SEARCH_CONFIGS = ("english", "dutch")

#: How much of a body or description is indexed. Equal to the default page
#: ceiling (``page_max_chars``), so a page is indexed whole. It exists for
#: file descriptions, which have no ceiling: Postgres refuses a ``tsvector``
#: past one megabyte, and without a bound one enormous description would
#: fail its own upload. Measured at this length, the worst realistic text --
#: nothing but unique words -- stays under that limit in both
#: configurations together. Text made only of unique words in four-byte
#: scripts can still exceed it, and such a write is refused by the database.
INDEXED_CHARS = 200_000

#: Snippets bold their matches as Markdown, the format every reader here speaks.
_HEADLINE_OPTIONS = "StartSel=**, StopSel=**, MaxWords=25, MinWords=8"


def _vector(heading: str, content: str) -> str:
    """Return the SQL expression for a row's search vector.

    Headings count as headline hits (weight A), content as supporting text
    (weight B), once per configuration in :data:`SEARCH_CONFIGS`. Every
    function in it is immutable, which a generated column requires.

    :param heading: SQL expression for the short identifying text
    :param content: SQL expression for the long text
    :returns: a ``tsvector`` expression
    """
    return " || ".join(
        f"setweight(to_tsvector('{config}', coalesce({heading}, '')), 'A') || "
        f"setweight(to_tsvector('{config}', "
        f"left(coalesce({content}, ''), {INDEXED_CHARS})), 'B')"
        for config in SEARCH_CONFIGS
    )


def search_statements() -> list[str]:
    """Return DDL for the stored search vectors.

    Piccolo has no syntax for a generated column, so this is raw DDL in the
    same way ``reef.rls.constraint_statements`` is. The tables' Piccolo
    definitions deliberately do not declare ``search_vector``: Piccolo names
    its columns explicitly in every statement, so an undeclared column is
    never written, and Postgres would refuse a write to a generated column
    anyway.

    Titles and filenames are the heading; bodies and descriptions the
    content. Changing the expression means dropping and re-adding the
    column in a new migration -- ``IF NOT EXISTS`` here will not notice.

    :returns: SQL statements to execute in order
    """
    statements = []
    for table, heading, content in (
        ("pages", "title", "body"),
        ("attachments", "filename", "description"),
    ):
        statements.append(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS search_vector "
            f"tsvector GENERATED ALWAYS AS ({_vector(heading, content)}) STORED"
        )
    return statements


def _headline(text: str) -> str:
    """Return the SQL expression for a snippet of ``text``.

    The snippet is cut under the English configuration unless only the Dutch
    reading of the query matches the text, because ``ts_headline`` highlights
    by stem and an English stemmer leaves a Dutch match unmarked.

    :param text: SQL expression for the text to excerpt
    :returns: a text expression
    """
    excerpt = f"left({text}, {INDEXED_CHARS})"
    return (
        f"CASE WHEN to_tsvector('english', {excerpt}) @@ query.english_any "
        f"OR NOT to_tsvector('dutch', {excerpt}) @@ query.dutch_any "
        f"THEN ts_headline('english', {text}, query.english_any, '{_HEADLINE_OPTIONS}') "
        f"ELSE ts_headline('dutch', {text}, query.dutch_any, '{_HEADLINE_OPTIONS}') END"
    )


#: How ``ts_rank`` weighs a page's length: 1 divides the rank by one plus
#: the logarithm of the length. Without it a long page outranks a short one
#: merely for repeating a common word. Chosen by measurement on the
#: benchmark in ``scripts/eval``: 0 found the right page in the first five
#: results for 50% of questions, 1 for 53%. Dividing by the length itself
#: (2) dropped to 22%, and ``ts_rank_cd`` to 34%.
_RANK_NORMALIZATION = 1

#: The query, parsed once under each configuration, in two readings.
#:
#: ``english`` and ``dutch`` are the query as written: every word must be
#: there. ``english_any`` and ``dutch_any`` are the same query with its
#: words as alternatives, so a page holding some of them still matches.
#: Search matches on the loose reading and ranks pages that satisfy the
#: strict one first. Before this a single absent word ruled a page out,
#: which is how a question in ordinary words found nothing.
#:
#: The loose reading is made by swapping the operator in the parsed query's
#: text. That is safe because the text holds lexemes, which contain no
#: spaces, so `` & `` can only be the operator. Quoted phrases keep their
#: ``<->`` and stay phrases. A query with an exclusion is left strict:
#: ``a | !b`` would match nearly every page.
_QUERY_SQL = """
    WITH strict AS (
        SELECT
            websearch_to_tsquery('english', {}) AS english,
            websearch_to_tsquery('dutch', {}) AS dutch
    ),
    query AS (
        SELECT
            english,
            dutch,
            CASE WHEN english::text LIKE '%!%' THEN english
                ELSE replace(english::text, ' & ', ' | ')::tsquery
            END AS english_any,
            CASE WHEN dutch::text LIKE '%!%' THEN dutch
                ELSE replace(dutch::text, ' & ', ' | ')::tsquery
            END AS dutch_any
        FROM strict
    ),
"""

#: Rank first, excerpt second. ``ts_headline`` re-parses the whole text, so
#: it runs only for the rows that survive the limit, not for every match.
_HITS_SQL = f"""
    hits AS (
        SELECT 'page' AS kind, pages.id, pages.cove_id,
            search_vector @@ (query.english || query.dutch) AS every_word,
            ts_rank(search_vector, query.english_any || query.dutch_any,
                {_RANK_NORMALIZATION}) AS rank,
            updated_at AS at
        FROM pages, query
        WHERE search_vector @@ (query.english_any || query.dutch_any){{page_filter}}
        UNION ALL
        SELECT 'file' AS kind, attachments.id, attachments.cove_id,
            search_vector @@ (query.english || query.dutch) AS every_word,
            ts_rank(search_vector, query.english_any || query.dutch_any,
                {_RANK_NORMALIZATION}) AS rank,
            created_at AS at
        FROM attachments, query
        WHERE search_vector @@ (query.english_any || query.dutch_any)
          AND status = {{{{}}}}{{file_filter}}
        ORDER BY every_word DESC, rank DESC, at DESC
        LIMIT {{{{}}}}
    )
"""


def _results_sql(order: str) -> str:
    """Return the statement that turns chosen hits into result rows.

    :param order: the ``ORDER BY`` expression, over the ``hits`` relation
    :returns: the final ``SELECT`` of a search
    """
    return f"""
    SELECT
        hits.kind,
        hits.id,
        COALESCE(pages.cove_id, attachments.cove_id) AS cove_id,
        pages.path,
        pages.title,
        attachments.object_key AS key,
        attachments.filename,
        CASE WHEN hits.kind = 'page'
            THEN {_headline("pages.body")}
            ELSE {_headline("attachments.description")}
        END AS snippet
    FROM hits
    CROSS JOIN query
    LEFT JOIN pages ON hits.kind = 'page' AND pages.id = hits.id
    LEFT JOIN attachments ON hits.kind = 'file' AND attachments.id = hits.id
    WHERE pages.id IS NOT NULL OR attachments.id IS NOT NULL
    ORDER BY {order}
"""


_KEYWORD_ORDER = "hits.every_word DESC, hits.rank DESC, hits.at DESC"

_RESULTS_SQL = _results_sql(_KEYWORD_ORDER)

#: The keyword ranking alone, for the fusion: which rows, in which order,
#: and whether a row is ``sure``. A row is sure when it has no vector to be
#: judged by: a file, or a page that is not embedded yet. In a short query
#: a page that holds every word is sure as well. See :func:`fuse` for what
#: that is worth. The first parameter says whether the query is short.
_RANKING_SQL = f"""
    SELECT hits.kind, hits.id,
        ({{}} AND hits.every_word) OR hits.kind = 'file' OR NOT EXISTS (
            SELECT 1 FROM page_embeddings e
            WHERE e.page_id = hits.id AND e.model_id = {{}} AND e.dims = {{}}
        ) AS sure
    FROM hits ORDER BY {_KEYWORD_ORDER}
"""

#: The rows the fusion chose, in its order. They pass through the same
#: joins as a keyword hit, so row security decides once more what is shown.
_CHOSEN_SQL = """
    hits AS (
        SELECT chosen.kind, chosen.id, chosen.ord
        FROM unnest({}::text[], {}::uuid[]) WITH ORDINALITY
            AS chosen(kind, id, ord)
    )
"""

_CHOSEN_RESULTS_SQL = _results_sql("hits.ord")

#: The keyword ranking alone, for a search that has no ranking by meaning
#: but will be read again by the reranker.
_KEYWORD_RANKING_SQL = f"SELECT hits.kind, hits.id FROM hits ORDER BY {_KEYWORD_ORDER}"

#: Each candidate page's pieces closest to the query, best first. The
#: window is computed over rows that passed ``WHERE``, so ``<=>`` never
#: meets a vector of another length.
_PASSAGES_SQL = """
    SELECT ranked.page_id, ranked.section_anchor
    FROM (
        SELECT e.page_id, e.section_anchor,
            row_number() OVER (
                PARTITION BY e.page_id ORDER BY e.embedding <=> {}::vector
            ) AS n
        FROM page_embeddings e
        JOIN pages ON pages.id = e.page_id
        WHERE e.page_id = ANY({}::uuid[]) AND e.model_id = {} AND e.dims = {}
    ) ranked
    WHERE ranked.n <= {}
    ORDER BY ranked.page_id, ranked.n
"""

#: The text of candidate pages. Whole for a page whose best pieces are
#: known, since any piece may be among them. Otherwise only as much as the
#: whole-page piece holds: that is all that will be read.
_PAGE_TEXT_SQL = (
    "SELECT id, title, description, "
    "CASE WHEN id = ANY({}::uuid[]) THEN body ELSE left(body, {}) END AS body "
    "FROM pages WHERE id = ANY({}::uuid[])"
)

_FILE_TEXT_SQL = (
    "SELECT id, filename, description FROM attachments "
    "WHERE id = ANY({}::uuid[]) AND status = {}"
)

#: Pages by meaning: each page counts for its closest piece. Only vectors of
#: the model that embedded the question can be compared with it, and only
#: ones of its length: ``<=>`` raises on two lengths. There is no vector
#: index, see :func:`reef.rls.embedding_statements`, so this compares every
#: vector the caller can see.
_SEMANTIC_SQL = """
    SELECT e.page_id AS id
    FROM page_embeddings e
    JOIN pages ON pages.id = e.page_id
    WHERE e.model_id = {} AND e.dims = {}%s
    GROUP BY e.page_id
    ORDER BY min(e.embedding <=> {}::vector), e.page_id
    LIMIT {}
"""

#: A quoted phrase or an excluded word. Such a query says exactly what a
#: page must or must not hold, and a ranking by meaning would bring back
#: the very pages it rules out. Any dash that does not follow a letter or
#: digit counts, because Postgres reads ``a - b`` and ``a(-b)`` as
#: exclusions too. That also catches ``a -> b`` and ``-5``, which only
#: costs those queries the ranking by meaning. ``check-up`` is a word.
_EXACT = re.compile(r'"|(?<!\w)-')

_COVE_FILTER = " AND cove_id = {}"
_PAGE_COVE_FILTER = " AND pages.cove_id = {}"


def _row_payload(row: dict, aliases: dict) -> dict:
    """Shape one result row for the tool payload.

    :param row: a row from the union query
    :param aliases: the caller's cove-id-to-alias map
    :returns: the fields a reader needs, keyed by kind
    """
    common = {
        "cove": aliases[row["cove_id"]],
        "kind": row["kind"],
        "snippet": row["snippet"],
    }
    if row["kind"] == "file":
        return {**common, "key": row["key"], "filename": row["filename"]}
    return {**common, "path": row["path"], "title": row["title"]}


def fuse(
    lexical: list[tuple[str, UUID, bool]], semantic: list[UUID]
) -> list[tuple[str, UUID]]:
    """Merge the keyword ranking and the meaning ranking into one.

    Reciprocal rank fusion: a row scores by its place in each ranking, not
    by the rankings' own scores, which cannot be compared. A row in both
    outranks a row at the same place in one.

    A place in the meaning ranking counts ``REEF_SEARCH_SEMANTIC_WEIGHT``
    times a
    place in the keyword ranking. Left at that, any page close in meaning
    would outrank the best keyword hit that is not, and two kinds of row
    would vanish from the first results: a file or a fresh page, which has
    no vector to be close with, and the one page that holds the exact
    identifier somebody typed, when the model does not place it among the
    closest. Which hits are sure is decided by :data:`_RANKING_SQL`. So a keyword hit marked sure that is missing from the meaning
    ranking is taken to stand as high there as it stands by words. Being
    absent costs it nothing. A sure hit that is in the meaning ranking is
    judged by its real place, like any other.

    :param lexical: kind, id and sureness of the keyword hits, best first
    :param semantic: ids of the pages closest in meaning, best first
    :returns: kind and id of every row in either ranking, best first
    """
    k = get_settings().search.rrf_k
    weight = get_settings().search.semantic_weight
    place = {page_id: rank for rank, page_id in enumerate(semantic, 1)}
    score: dict[tuple[str, UUID], float] = {}
    for rank, (kind, row_id, sure) in enumerate(lexical, 1):
        by_meaning = place.pop(row_id, None) if kind == "page" else None
        if sure and by_meaning is None:
            by_meaning = rank
        score[(kind, row_id)] = 1.0 / (k + rank) + (
            weight / (k + by_meaning) if by_meaning else 0.0
        )
    for page_id, rank in place.items():
        score[("page", page_id)] = weight / (k + rank)
    # sorted() is stable, so equal scores keep the keyword ranking's order.
    return sorted(score, key=lambda key: score[key], reverse=True)


async def _closest_pages(
    embedded: tuple[str, list[float]], cove_ids: list
) -> list[UUID]:
    """Rank the caller's pages by meaning.

    The statement runs inside a savepoint. If it fails, for whatever
    reason, the savepoint is rolled back and the search goes on with
    keywords: a failed statement would otherwise abort the transaction and
    the person would get an error instead of results.

    :param embedded: the model's name and the query's vector
    :param cove_ids: the one cove to stay inside, or an empty list
    :returns: page ids, closest first. Empty when the statement failed or
        no visible page has a vector of this model.
    """
    model_id, vector = embedded
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        rows = await Page.raw(
            _SEMANTIC_SQL % (_PAGE_COVE_FILTER if cove_ids else ""),
            model_id,
            len(vector),
            *cove_ids,
            embedding.literal(vector),
            get_settings().search.fusion_depth,
        )
    # Broad on purpose: whatever went wrong, the search must still answer.
    except Exception as error:  # noqa: BLE001
        await savepoint.rollback_to()
        log.warning("search answers by keywords alone (%s)", type(error).__name__)
        return []
    await savepoint.release()
    return [row["id"] for row in rows]


async def _best_anchors(
    page_ids: list[UUID], embedded: tuple[str, list[float]] | None, count: int
) -> dict[UUID, list[str | None]]:
    """Find each page's pieces closest to the query.

    Runs inside a savepoint, like :func:`_closest_pages`. On any failure
    the pages are read from their start instead.

    :param page_ids: the candidate pages
    :param embedded: the model's name and the query's vector, if any
    :param count: how many pieces per page
    :returns: the anchors of each page's best pieces, best first
    """
    if embedded is None or not page_ids:
        return {}
    model_id, vector = embedded
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        rows = await Page.raw(
            _PASSAGES_SQL,
            embedding.literal(vector),
            [str(page_id) for page_id in page_ids],
            model_id,
            len(vector),
            count,
        )
    # Broad on purpose: without the best pieces, a page's start will do.
    except Exception as error:  # noqa: BLE001
        await savepoint.rollback_to()
        log.warning("search reads pages from their start (%s)", type(error).__name__)
        return {}
    await savepoint.release()
    best: dict[UUID, list[str | None]] = {}
    for row in rows:
        best.setdefault(row["page_id"], []).append(row["section_anchor"])
    return best


@dataclass
class _Reading:
    """What phase 1 read for the reranker, before any text is cut."""

    candidates: list[tuple[str, UUID]]
    anchors: dict[UUID, list[str | None]] = field(default_factory=dict)
    pages: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)


async def _read_candidates(
    candidates: list[tuple[str, UUID]], embedded: tuple[str, list[float]] | None
) -> _Reading:
    """Read what the reranker will be sent of each candidate.

    Everything is read under the caller's row security, so the reranker can
    only ever be sent text the caller could open anyway. The reads run in a
    savepoint: if one fails, nothing is read again, and the search answers
    in the order it had.

    Cutting the pages into pieces is left to :func:`_documents`, outside
    the transaction: on long pages it takes long enough to matter.

    :param candidates: kind and id of the rows to read again, best first
    :param embedded: the model's name and the query's vector, if any
    :returns: what was read; no candidates if the reads failed
    """
    page_ids = [row_id for kind, row_id in candidates if kind == "page"]
    file_ids = [row_id for kind, row_id in candidates if kind == "file"]
    settings = get_settings()
    anchors = await _best_anchors(page_ids, embedded, settings.rerank.passages)
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        pages = (
            await Page.raw(
                _PAGE_TEXT_SQL,
                [str(page_id) for page_id in anchors],
                settings.embedding.page_chars,
                [str(page_id) for page_id in page_ids],
            )
            if page_ids
            else []
        )
        files = (
            await Page.raw(
                _FILE_TEXT_SQL,
                [str(file_id) for file_id in file_ids],
                AttachmentStatus.READY.value,
            )
            if file_ids
            else []
        )
    # Broad on purpose: without passages the search keeps its order.
    except Exception as error:  # noqa: BLE001
        await savepoint.rollback_to()
        log.warning("search keeps its order (%s)", type(error).__name__)
        return _Reading(candidates=[])
    await savepoint.release()
    return _Reading(candidates, anchors, list(pages), list(files))


def _documents(reading: _Reading) -> tuple[list[str], list[int]]:
    """Cut what was read into the passages the reranker reads.

    A page gives its pieces closest to the query, cut as for embedding, so
    each carries its title and heading in front. A page without vectors
    gives its start. A page with no text gives its title and description.
    A file gives its name and description. Pure, so it can run in a thread.

    :param reading: what :func:`_read_candidates` read
    :returns: the passages, and for each the position of its candidate
    """
    texts: dict[tuple[str, UUID], list[str]] = {}
    for row in reading.pages:
        title, body = row["title"] or "", row["body"] or ""
        description = row["description"] or summary(body)
        pieces = sections.split(title, description, body)
        if not pieces:
            label = "\n".join(part for part in (title, description) if part)
            texts[("page", row["id"])] = [label] if label else []
            continue
        by_anchor = {piece.anchor: piece.text for piece in pieces}
        texts[("page", row["id"])] = [
            by_anchor[anchor]
            for anchor in reading.anchors.get(row["id"], [])
            if anchor in by_anchor
        ] or [pieces[0].text]
    for row in reading.files:
        texts[("file", row["id"])] = [
            f"{row['filename']}\n\n{row['description'] or ''}".strip()
        ]
    documents: list[str] = []
    owners: list[int] = []
    for position, key in enumerate(reading.candidates):
        for text in texts.get(key, []):
            documents.append(text)
            owners.append(position)
    return documents, owners


def reorder(
    ranking: list[tuple[str, UUID]],
    count: int,
    owners: list[int],
    scores: list[float],
) -> list[tuple[str, UUID]]:
    """Put the first ``count`` rows of a ranking in the reranker's order.

    A row scores by its best passage. A candidate the reranker read nothing
    of keeps its own place. Rows past ``count`` keep their order and follow.

    :param ranking: kind and id of every row, best first
    :param count: how many rows were candidates
    :param owners: for each passage, the position of its row
    :param scores: for each passage, the reranker's score
    :returns: the ranking, reordered
    """
    best: dict[int, float] = {}
    for position, score in zip(owners, scores, strict=True):
        best[position] = max(best.get(position, score), score)
    read = iter(sorted(best, key=lambda position: (-best[position], position)))
    # A candidate the reranker read nothing of keeps its own place; the
    # ones it read fill the other places in its order.
    order = [
        position if position not in best else next(read) for position in range(count)
    ]
    return [ranking[position] for position in order] + ranking[count:]


async def _record(log_it: bool, principal: Principal, query: str, rows: list) -> None:
    """Record the search in the person's search log, if asked to.

    :param log_it: whether the caller asked for logging
    :param principal: the person who searched
    :param query: the search text
    :param rows: the result rows
    """
    if log_it:
        await searchlog.record(
            principal, query, [row["id"] for row in rows if row["kind"] == "page"]
        )


async def search_pages(
    principal: Principal,
    query: str,
    cove: str | None = None,
    limit: int = 10,
    *,
    log: bool = False,
    semantic: bool = True,
    rerank: bool = True,
) -> list[dict]:
    """Search every page and described file the caller can see, best match first.

    **By words.** ``websearch_to_tsquery`` parses the query, so plain
    words, quoted phrases, and ``-exclusions`` all work and malformed input
    cannot raise. A page need not hold every word: pages that do come
    first, then pages holding some of them. A query with an exclusion is
    the exception and stays strict. See :data:`_QUERY_SQL`.
    Files match on filename and description; only files whose bytes landed
    (status ``ready``) appear, because a hit the reader cannot open reads
    as reef losing their file.

    **By meaning.** Where there is an embedding service, the query is
    embedded and the pages closest to it are merged in, see :func:`fuse`.
    A page can then be found that shares no word with the query, so a
    search with vectors rarely comes back empty. Files have no vectors and
    are found by words only.

    **Read again.** Where there is a reranker, the first
    ``REEF_RERANK_CANDIDATES`` results are read next to the query and put
    in its order, see :mod:`reef.rerank`.

    A query with a quoted phrase or an exclusion is neither merged nor read
    again: it stays exact.

    **Never slow because of a model.** The search embeds the query first,
    then reads in one short transaction, then asks the reranker, then reads
    the results in a second short transaction. No database connection
    waits on a model. Called inside a transaction, it runs in that one.

    Search never fails because of the embedding service or the reranker.
    Without them, or when they are slow or down, the answer is the order
    it had without them.

    :param principal: the authenticated person
    :param query: words to search for
    :param cove: restrict to one cove by its alias; all coves when None
    :param limit: maximum results, clamped to a sane ceiling
    :param log: record the search in the person's search log. Set by the
        tools a person's assistant calls, not by scripts. See
        :mod:`reef.searchlog`, which records nothing unless switched on.
    :param semantic: merge in the ranking by meaning when there is one.
        False measures keyword search alone.
    :param rerank: let the reranker reorder the first results when there
        is one. False measures the search without it.
    :returns: one dict per hit — pages carry path and title, files key and
        filename; both carry the cove alias, kind, and a snippet
    :raises AccessDenied: when ``cove`` names no cove of the caller's
    """
    # Postgres text cannot hold a NUL byte, and one in a parameter fails the
    # whole statement rather than matching nothing.
    query = query.replace("\x00", "").strip()
    if not query:
        return []
    settings = get_settings()
    limit = max(1, min(limit, settings.search.max_results))
    exact = _EXACT.search(query) is not None
    embedded = await embedding.embed_question(query) if semantic and not exact else None
    reread = rerank and not exact and reranking.wanted()
    reading = _Reading(candidates=[])
    async with transaction_scope():
        aliases = await alias_map(principal)
        ready = AttachmentStatus.READY.value
        cove_filter = ""
        cove_ids: list = []
        if cove is not None:
            target = await resolve_cove(principal, cove)
            cove_filter = _COVE_FILTER
            cove_ids = [target.id]
        hits_sql = _QUERY_SQL + _HITS_SQL.format(
            page_filter=cove_filter, file_filter=cove_filter
        )
        closest = await _closest_pages(embedded, cove_ids) if embedded else []
        if not closest and not reread:
            # Keyword search, in one statement, as it always was.
            rows = await Page.raw(
                hits_sql + _RESULTS_SQL,
                query,
                query,
                *cove_ids,
                ready,
                *cove_ids,
                limit,
            )
            await _record(log, principal, query, rows)
            return [_row_payload(row, aliases) for row in rows]
        if closest:
            model_id, vector = embedded
            keyword = await Page.raw(
                hits_sql + _RANKING_SQL,
                query,
                query,
                *cove_ids,
                ready,
                *cove_ids,
                max(limit, settings.search.fusion_depth),
                len(query.split()) <= settings.search.sure_words,
                model_id,
                len(vector),
            )
            ranking = fuse(
                [(row["kind"], row["id"], row["sure"]) for row in keyword], closest
            )
        else:
            keyword = await Page.raw(
                hits_sql + _KEYWORD_RANKING_SQL,
                query,
                query,
                *cove_ids,
                ready,
                *cove_ids,
                max(limit, settings.rerank.candidates),
            )
            ranking = [(row["kind"], row["id"]) for row in keyword]
        if reread and len(ranking) > 1:
            reading = await _read_candidates(
                ranking[: get_settings().rerank.candidates], embedded
            )
    if reading.candidates:
        # In a thread: cutting long pages would hold up every other request.
        documents, owners = await asyncio.to_thread(_documents, reading)
        scores = await reranking.rerank(query, documents) if documents else None
        if scores is not None:
            ranking = reorder(ranking, len(reading.candidates), owners, scores)
    chosen = ranking[:limit]
    async with transaction_scope():
        await arm(principal)
        rows = (
            await Page.raw(
                _QUERY_SQL + _CHOSEN_SQL + _CHOSEN_RESULTS_SQL,
                query,
                query,
                [kind for kind, _ in chosen],
                [str(row_id) for _, row_id in chosen],
            )
            if chosen
            else []
        )
        await _record(log, principal, query, rows)
    return [_row_payload(row, aliases) for row in rows]
