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

import re
from uuid import UUID

from reef import embedding, searchlog
from reef.access import Principal, alias_map, resolve_cove
from reef.models import AttachmentStatus, Page

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

#: The keyword ranking alone, for the fusion: which rows, in which order.
_RANKING_SQL = f"SELECT hits.kind, hits.id FROM hits ORDER BY {_KEYWORD_ORDER}"

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

#: How many results of each ranking take part in the fusion.
FUSION_DEPTH = 50

#: The constant of reciprocal rank fusion. 60 is the value from the paper.
RRF_K = 60

#: How much a place in the meaning ranking counts, against 1 for a place in
#: the keyword ranking. Measured on the benchmark in ``scripts/eval`` on
#: 18 November 2026: 1, 2 and 3 found the right page in the first five
#: results for 45.7%, 45.7% and 47.4% of questions, against 37.5% for
#: keywords alone.
SEMANTIC_WEIGHT = 3.0

#: A quoted phrase or an excluded word. Such a query says exactly what a
#: page must or must not hold, and a ranking by meaning would bring back
#: the very pages it rules out.
_EXACT = re.compile(r'"|(?:^|\s)-\S')

_COVE_FILTER = " AND cove_id = {}"
_PAGE_COVE_FILTER = " AND pages.cove_id = {}"

_MAX_RESULTS = 50


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
    lexical: list[tuple[str, UUID]], semantic: list[UUID]
) -> list[tuple[str, UUID]]:
    """Merge the keyword ranking and the meaning ranking into one.

    Reciprocal rank fusion: a row scores by its place in each ranking, not
    by the rankings' own scores, which cannot be compared. A row in both
    outranks a row at the same place in one.

    :param lexical: kind and id of the keyword hits, best first
    :param semantic: ids of the pages closest in meaning, best first
    :returns: kind and id of every row in either ranking, best first
    """
    score: dict[tuple[str, UUID], float] = {}
    for rank, key in enumerate(lexical, 1):
        score[key] = 1.0 / (RRF_K + rank)
    for rank, page_id in enumerate(semantic, 1):
        key = ("page", page_id)
        score[key] = score.get(key, 0.0) + SEMANTIC_WEIGHT / (RRF_K + rank)
    # sorted() is stable, so equal scores keep the keyword ranking's order.
    return sorted(score, key=lambda key: score[key], reverse=True)


async def _closest_pages(query: str, cove_ids: list) -> list[UUID]:
    """Rank the caller's pages by meaning, if that is possible right now.

    :param query: the search text
    :param cove_ids: the one cove to stay inside, or an empty list
    :returns: page ids, closest first. Empty when the query is exact, when
        there is no embedding service, when it did not answer, or when no
        visible page has a vector of its model.
    """
    if _EXACT.search(query):
        return []
    embedded = await embedding.embed_question(query)
    if embedded is None:
        return []
    model_id, vector = embedded
    rows = await Page.raw(
        _SEMANTIC_SQL % (_PAGE_COVE_FILTER if cove_ids else ""),
        model_id,
        len(vector),
        *cove_ids,
        embedding.literal(vector),
        FUSION_DEPTH,
    )
    return [row["id"] for row in rows]


async def search_pages(
    principal: Principal,
    query: str,
    cove: str | None = None,
    limit: int = 10,
    *,
    log: bool = False,
    semantic: bool = True,
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
    search with vectors rarely comes back empty. A query with a quoted
    phrase or an exclusion is not merged: it stays exact. Files have no
    vectors and are found by words only.

    Search never fails because of the embedding service. Without one, or
    when it is slow or down, the answer is the keyword ranking.

    :param principal: the authenticated person
    :param query: words to search for
    :param cove: restrict to one cove by its alias; all coves when None
    :param limit: maximum results, clamped to a sane ceiling
    :param log: record the search in the person's search log. Set by the
        tools a person's assistant calls, not by scripts. See
        :mod:`reef.searchlog`, which records nothing unless switched on.
    :param semantic: merge in the ranking by meaning when there is one.
        False measures keyword search alone.
    :returns: one dict per hit — pages carry path and title, files key and
        filename; both carry the cove alias, kind, and a snippet
    :raises AccessDenied: when ``cove`` names no cove of the caller's
    """
    # Postgres text cannot hold a NUL byte, and one in a parameter fails the
    # whole statement rather than matching nothing.
    query = query.replace("\x00", "").strip()
    if not query:
        return []
    limit = max(1, min(limit, _MAX_RESULTS))
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
    closest = await _closest_pages(query, cove_ids) if semantic else []
    if closest:
        ranking = await Page.raw(
            hits_sql + _RANKING_SQL,
            query,
            query,
            *cove_ids,
            ready,
            *cove_ids,
            max(limit, FUSION_DEPTH),
        )
        chosen = fuse([(row["kind"], row["id"]) for row in ranking], closest)[:limit]
        rows = await Page.raw(
            _QUERY_SQL + _CHOSEN_SQL + _CHOSEN_RESULTS_SQL,
            query,
            query,
            [kind for kind, _ in chosen],
            [str(row_id) for _, row_id in chosen],
        )
    else:
        rows = await Page.raw(
            hits_sql + _RESULTS_SQL, query, query, *cove_ids, ready, *cove_ids, limit
        )
    if log:
        await searchlog.record(
            principal, query, [row["id"] for row in rows if row["kind"] == "page"]
        )
    return [_row_payload(row, aliases) for row in rows]
