"""Search across the caller's engrams and files, under RLS.

Two rankings, merged. Postgres full-text search finds engrams and files by
their words. Where the deployment has an embedding service, the stored
vectors find engrams by their meaning, and the two rankings are fused. See
:func:`search_engrams`. Without a service, or when it does not answer, search
is keyword search and nothing else changes.

With reading switched on (``REEF_FILE_TEXT``), a file is also found by the
words on its pages and by the meaning of its chunks (see
:mod:`reef.filechunks`), and a file hit names the page it was found on. With
it off, every statement is the one search ran before files had text.

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
because reef's engrams are written in both and an engram does not say which.
Stemming is per language: ``contracten`` reaches ``contract`` only through
the Dutch stemmer. A query is parsed under both configurations and the two
are ORed, so either reading of a word can match.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

from reef import embedding, filechunks, searchlog, sections
from reef import rerank as reranking
from reef.access import Principal, alias_map, arm, resolve_space
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.indexfields import summary
from reef.models import AttachmentStatus, Engram

log = logging.getLogger(__name__)

#: Text search configurations indexed for every row, in this order. Adding
#: one changes the stored column's expression, which a migration has to do.
SEARCH_CONFIGS = ("english", "dutch")

#: How much of a body or description is indexed. Equal to the default engram
#: ceiling (``REEF_ENGRAM_MAX_CHARS``), so an engram is indexed whole. It exists for
#: file descriptions, which have no ceiling: Postgres refuses a ``tsvector``
#: past one megabyte, and without a bound one enormous description would
#: fail its own upload. Measured at this length, the worst realistic text --
#: nothing but unique words -- stays under that limit in both
#: configurations together. Text made only of unique words in four-byte
#: scripts can still exceed it, and such a write is refused by the database.
INDEXED_CHARS = 200_000

#: Snippets bold their matches as Markdown, the format every reader here speaks.
_HEADLINE_OPTIONS = "StartSel=**, StopSel=**, MaxWords=25, MinWords=8"

#: How much of an engram's best piece a hit carries as its passage, in
#: characters. See ``passages`` in :func:`search_engrams`.
PASSAGE_CHARS = 1_500


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
    content. A file's page has no heading of its own, so its text is all
    content (weight B): a word in a page counts as a word in a body, not as
    one in a title. Changing the expression means dropping and re-adding the
    column in a new migration -- ``IF NOT EXISTS`` here will not notice.

    :returns: SQL statements to execute in order
    """
    statements = []
    for table, heading, content in (
        ("engrams", "title", "body"),
        ("attachments", "filename", "description"),
        ("file_pages", "NULL", "text"),
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


#: How ``ts_rank`` weighs an engram's length: 1 divides the rank by one plus
#: the logarithm of the length. Without it a long engram outranks a short one
#: merely for repeating a common word. Chosen by measurement on the
#: benchmark in ``scripts/eval``: 0 found the right engram in the first five
#: results for 50% of questions, 1 for 53%. Dividing by the length itself
#: (2) dropped to 22%, and ``ts_rank_cd`` to 34%.
_RANK_NORMALIZATION = 1

#: The query, parsed once under each configuration, in two readings.
#:
#: ``english`` and ``dutch`` are the query as written: every word must be
#: there. ``english_any`` and ``dutch_any`` are the same query with its
#: words as alternatives, so an engram holding some of them still matches.
#: Search matches on the loose reading and ranks engrams that satisfy the
#: strict one first. Before this a single absent word ruled an engram out,
#: which is how a question in ordinary words found nothing.
#:
#: The loose reading is made by swapping the operator in the parsed query's
#: text. That is safe because the text holds lexemes, which contain no
#: spaces, so `` & `` can only be the operator. Quoted phrases keep their
#: ``<->`` and stay phrases. A query with an exclusion is left strict:
#: ``a | !b`` would match nearly every engram.
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


def _hits_sql(page: str, files: str) -> str:
    """Return the ``hits`` relation: every row that matches by words, ranked.

    Rank first, excerpt second. ``ts_headline`` re-parses the whole text, so
    it runs only for the rows that survive the limit, not for every match.

    :param page: what follows an engram's ``at`` column: nothing, or the
        ``page`` column a file hit carries
    :param files: the branch that finds files
    :returns: the relation, with ``{engram_filter}`` and ``{file_filter}``
        left to fill in
    """
    return f"""
    hits AS (
        SELECT 'page' AS kind, engrams.id, engrams.space_id,
            search_vector @@ (query.english || query.dutch) AS every_word,
            ts_rank(search_vector, query.english_any || query.dutch_any,
                {_RANK_NORMALIZATION}) AS rank,
            updated_at AS at{page}
        FROM engrams, query
        WHERE search_vector @@ (query.english_any || query.dutch_any){{engram_filter}}
        UNION ALL
{files}
        ORDER BY every_word DESC, rank DESC, at DESC
        LIMIT {{{{}}}}
    )
"""


#: Files by their filename and description, as before files had text.
_FILE_HITS = f"""        SELECT 'file' AS kind, attachments.id, attachments.space_id,
            search_vector @@ (query.english || query.dutch) AS every_word,
            ts_rank(search_vector, query.english_any || query.dutch_any,
                {_RANK_NORMALIZATION}) AS rank,
            created_at AS at
        FROM attachments, query
        WHERE search_vector @@ (query.english_any || query.dutch_any)
          AND status = {{{{}}}}{{file_filter}}"""

#: One file's best match by words: its own row (filename and description,
#: no page) or one of its pages, whichever holds every word of the query,
#: then ranks higher. On a tie the file's own row wins, then the first page.
#: Needs ``attachments`` and ``query`` in scope. Both the keyword ranking and
#: the rows the fusion chose use it, so both name the same page.
_FILE_MATCH_SQL = f"""
            SELECT attachments.search_vector @@ (query.english || query.dutch)
                    AS every_word,
                ts_rank(attachments.search_vector,
                    query.english_any || query.dutch_any, {_RANK_NORMALIZATION}) AS rank,
                NULL::int AS page
            WHERE attachments.search_vector @@ (query.english_any || query.dutch_any)
            UNION ALL
            SELECT file_pages.search_vector @@ (query.english || query.dutch),
                ts_rank(file_pages.search_vector,
                    query.english_any || query.dutch_any, {_RANK_NORMALIZATION}),
                file_pages.number
            FROM file_pages
            WHERE file_pages.attachment_id = attachments.id
              AND file_pages.search_vector @@ (query.english_any || query.dutch_any)
            ORDER BY every_word DESC, rank DESC, page NULLS FIRST
            LIMIT 1
        """

#: Files by their filename, description or any page, each once, by its best
#: match. A page is weight B, like a body, so it ranks below a filename.
_FILE_PAGE_HITS = f"""        SELECT 'file' AS kind, attachments.id, attachments.space_id,
            best.every_word, best.rank, created_at AS at, best.page
        FROM attachments
        CROSS JOIN query
        CROSS JOIN LATERAL ({_FILE_MATCH_SQL}) best
        WHERE status = {{{{}}}}{{file_filter}}"""

_HITS_SQL = _hits_sql("", _FILE_HITS)

#: As :data:`_HITS_SQL`, with the words on a file's pages. Each row carries
#: ``page``: the page a file was found on, or None.
_PAGE_HITS_SQL = _hits_sql(", NULL::int AS page", _FILE_PAGE_HITS)


def _results_sql(order: str, pages: bool = False) -> str:
    """Return the statement that turns chosen hits into result rows.

    :param order: the ``ORDER BY`` expression, over the ``hits`` relation
    :param pages: whether ``hits`` carries a ``page``. A file hit then names
        that page, and its snippet comes from that page's text. A page that is
        gone since, or has no text, gives none, and the description is used.
    :returns: the final ``SELECT`` of a search
    """
    page = shown = join = ""
    if pages:
        page = "\n        shown.number AS page,"
        shown = (
            "\n            WHEN shown.number IS NOT NULL"
            f"\n            THEN {_headline('shown.text')}"
        )
        join = (
            "\n    LEFT JOIN file_pages shown ON shown.attachment_id = attachments.id"
            "\n        AND shown.number = hits.page AND shown.text <> ''"
        )
    return f"""
    SELECT
        hits.kind,
        hits.id,
        COALESCE(engrams.space_id, attachments.space_id) AS space_id,
        engrams.path,
        engrams.title,
        attachments.object_key AS key,
        attachments.filename,{page}
        CASE WHEN hits.kind = 'page'
            THEN {_headline("engrams.body")}{shown}
            ELSE {_headline("attachments.description")}
        END AS snippet
    FROM hits
    CROSS JOIN query
    LEFT JOIN engrams ON hits.kind = 'page' AND engrams.id = hits.id
    LEFT JOIN attachments ON hits.kind = 'file' AND attachments.id = hits.id{join}
    WHERE engrams.id IS NOT NULL OR attachments.id IS NOT NULL
    ORDER BY {order}
"""


_KEYWORD_ORDER = "hits.every_word DESC, hits.rank DESC, hits.at DESC"

_RESULTS_SQL = _results_sql(_KEYWORD_ORDER)

_PAGE_RESULTS_SQL = _results_sql(_KEYWORD_ORDER, pages=True)

#: The keyword ranking alone, for the fusion: which rows, in which order,
#: and whether a row is ``sure``. See :func:`fuse` for what that is worth.
#: The first parameter says whether the query is short.
#:
#: - An engram that is not embedded yet is sure: it has no vector to be judged
#:   by, and will have one soon.
#: - In a short query, an engram that holds every word is sure, and so is a
#:   file. Somebody who types a name wants the engram or file that has it.
#: - In a whole question a file is not sure. Nearly every file shares some
#:   word with a question, and credit for a meaning nobody measured let
#:   files crowd out the engrams that answer it. Measured on 19 November 2026
#:   on real engrams and files, 40 questions: with that credit 82.5% found
#:   a right engram in the first five, without it 95.0%.
_RANKING_SQL = f"""
    SELECT hits.kind, hits.id,
        ({{}} AND (hits.every_word OR hits.kind = 'file'))
        OR (hits.kind = 'page' AND NOT EXISTS (
            SELECT 1 FROM engram_embeddings e
            WHERE e.engram_id = hits.id AND e.model_id = {{}} AND e.dims = {{}}
        )) AS sure
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

#: As :data:`_CHOSEN_SQL`, with a page for each row: the page the passage
#: comes from. The third array gives the page of a file's closest chunk, and
#: the fourth says whether the file has one. A file with a closest chunk is
#: shown at that chunk's page, which is None for the whole-file chunk; only a
#: file without one is looked for by words, and shown at its best page.
_CHOSEN_PAGES_SQL = f"""
    hits AS (
        SELECT chosen.kind, chosen.id, chosen.ord,
            CASE WHEN chosen.closest THEN chosen.page ELSE best.page END AS page
        FROM unnest({{}}::text[], {{}}::uuid[], {{}}::int[], {{}}::boolean[])
            WITH ORDINALITY AS chosen(kind, id, page, closest, ord)
        CROSS JOIN query
        LEFT JOIN attachments ON chosen.kind = 'file' AND NOT chosen.closest
            AND attachments.id = chosen.id
        LEFT JOIN LATERAL ({_FILE_MATCH_SQL}) best ON true
    )
"""

_CHOSEN_PAGE_RESULTS_SQL = _results_sql("hits.ord", pages=True)

#: The keyword ranking alone, for a search that has no ranking by meaning
#: but will be read again by the reranker.
_KEYWORD_RANKING_SQL = f"SELECT hits.kind, hits.id FROM hits ORDER BY {_KEYWORD_ORDER}"

#: Each candidate engram's pieces closest to the query, best first. The
#: window is computed over rows that passed ``WHERE``, so ``<=>`` never
#: meets a vector of another length.
_PASSAGES_SQL = """
    SELECT ranked.engram_id, ranked.section_anchor
    FROM (
        SELECT e.engram_id, e.section_anchor,
            row_number() OVER (
                PARTITION BY e.engram_id ORDER BY e.embedding <=> {}::vector
            ) AS n
        FROM engram_embeddings e
        JOIN engrams ON engrams.id = e.engram_id
        WHERE e.engram_id = ANY({}::uuid[]) AND e.model_id = {} AND e.dims = {}
    ) ranked
    WHERE ranked.n <= {}
    ORDER BY ranked.engram_id, ranked.n
"""

#: The text of candidate engrams. Whole for an engram whose best pieces are
#: known, since any piece may be among them. Otherwise only as much as the
#: whole-engram piece holds: that is all that will be read.
_ENGRAM_TEXT_SQL = (
    "SELECT id, title, description, "
    "CASE WHEN id = ANY({}::uuid[]) THEN body ELSE left(body, {}) END AS body "
    "FROM engrams WHERE id = ANY({}::uuid[])"
)

_FILE_TEXT_SQL = (
    "SELECT id, filename, description FROM attachments "
    "WHERE id = ANY({}::uuid[]) AND status = {}"
)

#: Each candidate file's chunks closest to the query, best first, as
#: :data:`_PASSAGES_SQL` for engrams. Joined to ``attachments`` so only a
#: ready file the caller can see is read. Equal distances fall to the chunk
#: anchor, so the page a hit names does not change from one search to the next.
_FILE_PASSAGES_SQL = """
    SELECT ranked.attachment_id, ranked.chunk_anchor
    FROM (
        SELECT f.attachment_id, f.chunk_anchor,
            row_number() OVER (
                PARTITION BY f.attachment_id
                ORDER BY f.embedding <=> {}::vector, f.chunk_anchor
            ) AS n
        FROM file_embeddings f
        JOIN attachments ON attachments.id = f.attachment_id
        WHERE f.attachment_id = ANY({}::uuid[]) AND f.model_id = {} AND f.dims = {}
          AND attachments.status = {}
    ) ranked
    WHERE ranked.n <= {}
    ORDER BY ranked.attachment_id, ranked.n
"""

#: What some files' chunks are cut from again, and nothing more. One
#: statement for every file.
#:
#: Each wanted row names a file and a page. That page starts a chunk, which
#: also holds the short pages after it, up to the next page of at least
#: ``REEF_EMBEDDING_PIECE_MIN_CHARS`` characters: see
#: :func:`reef.filechunks.page_chunks`. Those pages come whole (``in_chunk``).
#:
#: A wanted row without a page asks for the start of the text, which the
#: whole-file chunk holds: the first pages, cut to ``start`` characters, up
#: to the page that reaches that length (``in_start``). Every row says how
#: many pages the file has, which decides whether a chunk names its page.
#:
#: A long file is not read whole to find these pages. Where a page starts in
#: the text is bounded by its stored bytes alone: ``octet_length`` reads the
#: stored size without unpacking the text, and a character takes at most four
#: bytes, so a quarter of the bytes never places a page later than it is. A
#: few pages too many may come; no page the start needs is left out. Whether
#: a page is short shows in its first characters, because readers store
#: pages stripped.
_FILE_PAGES_SQL = """
    WITH sizes AS (SELECT {}::int AS start, {}::int AS piece_min),
    wanted AS (
        SELECT DISTINCT w.attachment_id, w.number
        FROM unnest({}::uuid[], {}::int[]) AS w(attachment_id, number)
    ),
    placed AS (
        SELECT p.attachment_id, p.number, p.text,
            count(*) OVER (PARTITION BY p.attachment_id) AS pages,
            coalesce(sum(octet_length(p.text) / 4
                + CASE WHEN octet_length(p.text) > 0 THEN 2 ELSE 0 END) OVER (
                    PARTITION BY p.attachment_id ORDER BY p.number
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ), 0) AS least_before
        FROM file_pages p
        WHERE p.attachment_id IN (SELECT attachment_id FROM wanted)
    ),
    runs AS (
        SELECT w.attachment_id, w.number AS first, (
            SELECT q.number FROM file_pages q
            WHERE q.attachment_id = w.attachment_id AND q.number > w.number
              AND length(btrim(left(q.text, 2 * sizes.piece_min))) >= sizes.piece_min
            ORDER BY q.number
            LIMIT 1
        ) AS stop
        FROM wanted w CROSS JOIN sizes
        WHERE w.number IS NOT NULL
    ),
    marked AS (
        SELECT placed.attachment_id, placed.number, placed.pages, placed.text,
            placed.least_before,
            EXISTS (
                SELECT 1 FROM runs
                WHERE runs.attachment_id = placed.attachment_id
                  AND placed.number >= runs.first
                  AND (runs.stop IS NULL OR placed.number < runs.stop)
            ) AS in_chunk,
            placed.least_before < sizes.start AND EXISTS (
                SELECT 1 FROM wanted
                WHERE wanted.attachment_id = placed.attachment_id
                  AND wanted.number IS NULL
            ) AS in_start
        FROM placed CROSS JOIN sizes
    )
    SELECT marked.attachment_id, marked.number, marked.pages,
        marked.in_chunk, marked.in_start,
        CASE WHEN marked.in_chunk THEN marked.text
            ELSE left(marked.text, (sizes.start - marked.least_before)::int)
        END AS text
    FROM marked CROSS JOIN sizes
    WHERE marked.in_chunk OR marked.in_start
    ORDER BY marked.attachment_id, marked.number
"""

#: Engrams by meaning: each engram counts for its closest piece. Only vectors of
#: the model that embedded the question can be compared with it, and only
#: ones of its length: ``<=>`` raises on two lengths. There is no vector
#: index, see :func:`reef.rls.embedding_statements`, so this compares every
#: vector the caller can see.
_SEMANTIC_SQL = """
    SELECT e.engram_id AS id
    FROM engram_embeddings e
    JOIN engrams ON engrams.id = e.engram_id
    WHERE e.model_id = {} AND e.dims = {}%s
    GROUP BY e.engram_id
    ORDER BY min(e.embedding <=> {}::vector), e.engram_id
    LIMIT {}
"""

#: Engrams and files by meaning, in one ranking: an engram counts for its
#: closest piece, a file for its closest chunk. As :data:`_SEMANTIC_SQL`, only
#: vectors of the question's model and length are compared. A file must be
#: ready, as for a search by words; the join to ``attachments`` also puts its
#: row security in front of its vectors'. The first parameter is the
#: question's vector, compared in both halves.
_SEMANTIC_FILES_SQL = """
    WITH question AS (SELECT {}::vector AS vector)
    SELECT closest.kind, closest.id
    FROM (
        SELECT 'page' AS kind, e.engram_id AS id,
            min(e.embedding <=> question.vector) AS distance
        FROM engram_embeddings e
        JOIN engrams ON engrams.id = e.engram_id
        CROSS JOIN question
        WHERE e.model_id = {} AND e.dims = {}%s
        GROUP BY e.engram_id
        UNION ALL
        SELECT 'file', f.attachment_id, min(f.embedding <=> question.vector)
        FROM file_embeddings f
        JOIN attachments ON attachments.id = f.attachment_id
        CROSS JOIN question
        WHERE f.model_id = {} AND f.dims = {} AND attachments.status = {}%s
        GROUP BY f.attachment_id
    ) closest
    ORDER BY closest.distance, closest.id
    LIMIT {}
"""

#: A quoted phrase or an excluded word. Such a query says exactly what an
#: engram must or must not hold, and a ranking by meaning would bring back
#: the very engrams it rules out. Any dash that does not follow a letter or
#: digit counts, because Postgres reads ``a - b`` and ``a(-b)`` as
#: exclusions too. That also catches ``a -> b`` and ``-5``, which only
#: costs those queries the ranking by meaning. ``check-up`` is a word.
_EXACT = re.compile(r'"|(?<!\w)-')

_SPACE_FILTER = " AND space_id = {}"
_SPACE_FILTER_WHERE = " WHERE space_id = {}"

#: How many engrams the caller can see; see :func:`_large_enough`.
_COUNT_SQL = "SELECT count(*) AS n FROM engrams"
_ENGRAM_SPACE_FILTER = " AND engrams.space_id = {}"
_FILE_SPACE_FILTER = " AND attachments.space_id = {}"


def _row_payload(
    row: dict, aliases: dict, passages: dict[UUID, str] | None = None
) -> dict:
    """Shape one result row for the tool payload.

    :param row: a row from the union query
    :param aliases: the caller's space-id-to-alias map
    :param passages: each engram's or file's passage, when they were asked
        for; a hit without one gets its snippet. None adds no passage.
    :returns: the fields a reader needs, keyed by kind. A file hit carries
        ``page``: the page it was found on, or None.
    """
    common = {
        "cove": aliases[row["space_id"]],
        "kind": row["kind"],
        "snippet": row["snippet"],
    }
    if row["kind"] == "file":
        # Without reading, no statement gives a page.
        page = row.get("page")
        payload = {
            **common,
            "key": row["key"],
            "filename": row["filename"],
            "page": page,
        }
    else:
        payload = {**common, "path": row["path"], "title": row["title"]}
    if passages is not None:
        payload["passage"] = passages.get(row["id"]) or row["snippet"]
    return payload


def fuse(
    lexical: list[tuple[str, UUID, bool]], semantic: list[tuple[str, UUID]]
) -> list[tuple[str, UUID]]:
    """Merge the keyword ranking and the meaning ranking into one.

    Reciprocal rank fusion: a row scores by its place in each ranking, not
    by the rankings' own scores, which cannot be compared. A row in both
    outranks a row at the same place in one.

    A place in the meaning ranking counts ``REEF_SEARCH_SEMANTIC_WEIGHT``
    times a place in the keyword ranking. Left at that, any engram close in meaning
    would outrank the best keyword hit that is not, and two kinds of row
    would vanish from the first results: a file or a fresh engram, which has
    no vector to be close with, and the one engram that holds the exact
    identifier somebody typed, when the model does not place it among the
    closest. Which hits are sure is decided by :data:`_RANKING_SQL`. So a keyword hit marked sure that is missing from the meaning
    ranking is taken to stand as high there as it stands by words. Being
    absent costs it nothing. A sure hit that is in the meaning ranking is
    judged by its real place, like any other.

    A row is its kind and id together: an engram and a file are two rows,
    whatever their ids.

    :param lexical: kind, id and sureness of the keyword hits, best first
    :param semantic: kind and id of the engrams and files closest in
        meaning, best first
    :returns: kind and id of every row in either ranking, best first
    """
    k = get_settings().search.rrf_k
    weight = get_settings().search.semantic_weight
    place = {row: rank for rank, row in enumerate(semantic, 1)}
    score: dict[tuple[str, UUID], float] = {}
    for rank, (kind, row_id, sure) in enumerate(lexical, 1):
        by_meaning = place.pop((kind, row_id), None)
        if sure and by_meaning is None:
            by_meaning = rank
        score[(kind, row_id)] = 1.0 / (k + rank) + (
            weight / (k + by_meaning) if by_meaning else 0.0
        )
    for row, rank in place.items():
        score[row] = weight / (k + rank)
    # sorted() is stable, so equal scores keep the keyword ranking's order.
    return sorted(score, key=lambda key: score[key], reverse=True)


async def _closest(
    embedded: tuple[str, list[float]], space_ids: list, files: bool
) -> list[tuple[str, UUID]]:
    """Rank the caller's engrams, and with reading on their files, by meaning.

    The statement runs inside a savepoint. If it fails, for whatever
    reason, the savepoint is rolled back and the search goes on with
    keywords: a failed statement would otherwise abort the transaction and
    the person would get an error instead of results.

    :param embedded: the model's name and the query's vector
    :param space_ids: the one space to stay inside, or an empty list
    :param files: rank files by their chunks too, see
        :data:`_SEMANTIC_FILES_SQL`. False runs the statement search ran
        before files had text.
    :returns: kind and id of each row, closest first. Empty when the
        statement failed or nothing visible has a vector of this model.
    """
    model_id, vector = embedded
    depth = get_settings().search.fusion_depth
    if files:
        statement = _SEMANTIC_FILES_SQL % (
            (_ENGRAM_SPACE_FILTER, _FILE_SPACE_FILTER) if space_ids else ("", "")
        )
        params = [
            embedding.literal(vector),
            model_id,
            len(vector),
            *space_ids,
            model_id,
            len(vector),
            AttachmentStatus.READY.value,
            *space_ids,
            depth,
        ]
    else:
        statement = _SEMANTIC_SQL % (_ENGRAM_SPACE_FILTER if space_ids else "")
        params = [model_id, len(vector), *space_ids, embedding.literal(vector), depth]
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        rows = await Engram.raw(statement, *params)
    # Broad on purpose: whatever went wrong, the search must still answer.
    except Exception as error:  # noqa: BLE001
        await savepoint.rollback_to()
        log.warning("search answers by keywords alone (%s)", type(error).__name__)
        return []
    await savepoint.release()
    if files:
        return [(row["kind"], row["id"]) for row in rows]
    return [("page", row["id"]) for row in rows]


async def _nearest(
    statement: str, params: list, owner: str, anchor: str, failed: str
) -> dict[UUID, list[str | None]]:
    """Run a statement that lists each owner's pieces closest to the query.

    Runs inside a savepoint, like :func:`_closest`. On any failure it reads
    nothing, and the caller reads its rows from their start instead.

    :param statement: :data:`_PASSAGES_SQL` or :data:`_FILE_PASSAGES_SQL`
    :param params: the statement's values
    :param owner: the column that names the engram or file
    :param anchor: the column that names the piece
    :param failed: what to log on failure, with ``%s`` for the error's class.
        A fixed text: nothing of the query or the rows goes in a log.
    :returns: the anchors of each owner's best pieces, best first
    """
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        rows = await Engram.raw(statement, *params)
    # Broad on purpose: without the best pieces, a row's start will do.
    except Exception as error:  # noqa: BLE001
        await savepoint.rollback_to()
        log.warning(failed, type(error).__name__)
        return {}
    await savepoint.release()
    best: dict[UUID, list[str | None]] = {}
    for row in rows:
        best.setdefault(row[owner], []).append(row[anchor])
    return best


async def _best_anchors(
    engram_ids: list[UUID], embedded: tuple[str, list[float]] | None, count: int
) -> dict[UUID, list[str | None]]:
    """Find each engram's pieces closest to the query.

    Runs inside a savepoint, like :func:`_closest`. On any failure the
    engrams are read from their start instead.

    :param engram_ids: the candidate engrams
    :param embedded: the model's name and the query's vector, if any
    :param count: how many pieces per engram
    :returns: the anchors of each engram's best pieces, best first
    """
    if embedded is None or not engram_ids:
        return {}
    model_id, vector = embedded
    return await _nearest(
        _PASSAGES_SQL,
        [
            embedding.literal(vector),
            [str(engram_id) for engram_id in engram_ids],
            model_id,
            len(vector),
            count,
        ],
        "engram_id",
        "section_anchor",
        "search reads engrams from their start (%s)",
    )


async def _best_chunks(
    file_ids: list[UUID], embedded: tuple[str, list[float]] | None, count: int
) -> dict[UUID, list[str | None]]:
    """Find each file's chunks closest to the query.

    One statement for every file, see :data:`_FILE_PASSAGES_SQL`. A file
    without vectors of the question's model has none. On any failure no
    file has any, and each is shown by its best page by words.

    :param file_ids: the candidate files
    :param embedded: the model's name and the query's vector, if any
    :param count: how many chunks per file
    :returns: the anchors of each file's best chunks, best first; None is
        the whole-file chunk
    """
    if embedded is None or not file_ids:
        return {}
    model_id, vector = embedded
    return await _nearest(
        _FILE_PASSAGES_SQL,
        [
            embedding.literal(vector),
            [str(file_id) for file_id in file_ids],
            model_id,
            len(vector),
            AttachmentStatus.READY.value,
            count,
        ],
        "attachment_id",
        "chunk_anchor",
        "search reads files from their start (%s)",
    )


@dataclass
class _Pages:
    """What was read of one file's pages to cut some of its chunks again.

    ``chunk_pages`` holds each wanted chunk's page and the short pages after
    it, whole. ``head`` holds the first pages, cut, for the start of the
    text, of which ``start_chars`` characters were asked for. ``count`` is
    how many pages the file has.
    """

    count: int = 0
    chunk_pages: list[tuple[int, str]] = field(default_factory=list)
    head: list[str] = field(default_factory=list)
    start_chars: int = 0

    @property
    def start(self) -> str:
        """The start of the file's text, as the whole-file chunk holds it.

        Only as long as was asked for: a page cut short is followed by the
        next, and past that length their join is not the file's text.
        """
        joined = "\n\n".join(text for text in map(str.strip, self.head) if text)
        return joined[: self.start_chars]


async def _read_pages(
    wanted: dict[UUID, set[int | None]], start_chars: int
) -> dict[UUID, _Pages]:
    """Read what some files' chunks are cut from, in one statement.

    Only the pages the wanted chunks need, see :data:`_FILE_PAGES_SQL`, so a
    file of 200 pages is not read whole for one passage. Under the caller's
    row security, like every read here. Call it inside a savepoint: it
    raises as the statement does.

    :param wanted: for each file, the pages whose chunks are wanted; None
        wants the start of the text, for the whole-file chunk
    :param start_chars: how much of the start is wanted
    :returns: what was read of each file that has pages
    """
    ids: list[str] = []
    numbers: list[int | None] = []
    for file_id, pages in wanted.items():
        for number in pages:
            ids.append(str(file_id))
            numbers.append(number)
    rows = await Engram.raw(
        _FILE_PAGES_SQL,
        start_chars,
        get_settings().embedding.piece_min_chars,
        ids,
        numbers,
    )
    read: dict[UUID, _Pages] = {}
    for row in rows:
        pages = read.setdefault(
            row["attachment_id"],
            _Pages(count=row["pages"], start_chars=start_chars),
        )
        if row["in_chunk"]:
            pages.chunk_pages.append((row["number"], row["text"]))
        if row["in_start"]:
            pages.head.append(row["text"])
    return read


def _wanted_pages(anchors: list[str | None]) -> set[int | None]:
    """Say which pages the chunks with these anchors are cut from.

    :param anchors: a file's chunk anchors; none for a file without vectors
    :returns: the page each chunk starts on; None for the whole-file chunk,
        which needs the start of the text. A file without vectors needs it
        too: the reranker then reads it by its start.
    """
    return {filechunks.page_of(anchor) for anchor in anchors} or {None}


def _file_chunks(row: dict, pages: _Pages, anchors: list[str | None]) -> list[str]:
    """Cut a file's chunks with these anchors out of what was read of it.

    Each comes out as it was cut for embedding, see
    :func:`reef.filechunks.page_chunks` and :func:`reef.filechunks.whole`.
    A chunk the file no longer has, read again since it was embedded, gives
    nothing.

    :param row: the file's id, filename and description
    :param pages: what :func:`_read_pages` read of it
    :param anchors: the chunks wanted, best first
    :returns: their texts, in that order
    """
    filename, description = row["filename"] or "", row["description"] or ""
    cut = {
        chunk.anchor: chunk.text
        for chunk in filechunks.page_chunks(
            filename, description, pages.chunk_pages, page_count=pages.count
        )
    }
    texts: list[str] = []
    for anchor in anchors:
        if anchor is None:
            texts.append(filechunks.whole(filename, description, pages.start).text)
        elif anchor in cut:
            texts.append(cut[anchor])
    return texts


@dataclass
class _Reading:
    """What phase 1 read for the reranker, before any text is cut.

    ``file_text`` says whether reading is on. With it off, a file is read by
    its name and description, as before files had text, and ``chunks`` and
    ``pages`` stay empty.
    """

    candidates: list[tuple[str, UUID]]
    anchors: dict[UUID, list[str | None]] = field(default_factory=dict)
    engrams: list[dict] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    file_text: bool = False
    chunks: dict[UUID, list[str | None]] = field(default_factory=dict)
    pages: dict[UUID, _Pages] = field(default_factory=dict)


async def _read_candidates(
    candidates: list[tuple[str, UUID]],
    embedded: tuple[str, list[float]] | None,
    file_text: bool,
) -> _Reading:
    """Read what the reranker will be sent of each candidate.

    Everything is read under the caller's row security, so the reranker can
    only ever be sent text the caller could open anyway. The reads run in a
    savepoint: if one fails, nothing is read again, and the search answers
    in the order it had.

    Cutting the engrams into pieces, and the files into chunks, is left to
    :func:`_documents`, outside the transaction: on long engrams and files
    it takes long enough to matter.

    :param candidates: kind and id of the rows to read again, best first
    :param embedded: the model's name and the query's vector, if any
    :param file_text: whether reading is on: a file is then read by its
        chunks closest to the query, which needs its pages
    :returns: what was read; no candidates if the reads failed
    """
    engram_ids = [row_id for kind, row_id in candidates if kind == "page"]
    file_ids = [row_id for kind, row_id in candidates if kind == "file"]
    settings = get_settings()
    anchors = await _best_anchors(engram_ids, embedded, settings.rerank.passages)
    chunks = (
        await _best_chunks(file_ids, embedded, settings.rerank.passages)
        if file_text
        else {}
    )
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        engrams = (
            await Engram.raw(
                _ENGRAM_TEXT_SQL,
                [str(engram_id) for engram_id in anchors],
                settings.embedding.engram_chars,
                [str(engram_id) for engram_id in engram_ids],
            )
            if engram_ids
            else []
        )
        files = (
            await Engram.raw(
                _FILE_TEXT_SQL,
                [str(file_id) for file_id in file_ids],
                AttachmentStatus.READY.value,
            )
            if file_ids
            else []
        )
        pages = (
            await _read_pages(
                {
                    file_id: _wanted_pages(chunks.get(file_id, []))
                    for file_id in file_ids
                },
                settings.embedding.engram_chars,
            )
            if file_text and file_ids
            else {}
        )
    # Broad on purpose: without passages the search keeps its order.
    except Exception as error:  # noqa: BLE001
        await savepoint.rollback_to()
        log.warning("search keeps its order (%s)", type(error).__name__)
        return _Reading(candidates=[])
    await savepoint.release()
    return _Reading(
        candidates=candidates,
        anchors=anchors,
        engrams=list(engrams),
        files=list(files),
        file_text=file_text,
        chunks=chunks,
        pages=pages,
    )


def _file_documents(row: dict, pages: _Pages, anchors: list[str | None]) -> list[str]:
    """Give the passages the reranker reads of one file.

    Its chunks closest to the query, cut again as they were cut for
    embedding, so each carries the filename, the description and the page
    in front. A file without vectors gives what its whole-file chunk holds:
    its name, description and the start of its text. They are laid out as
    the reranker read a file before files had text, so a file without text
    reads exactly as it did then. A file whose chunks changed since it was
    embedded gives its name and description: its start was not read.

    :param row: the file's id, filename and description
    :param pages: what :func:`_read_pages` read of it
    :param anchors: its best chunks' anchors, best first
    :returns: the passages
    """
    best = _file_chunks(row, pages, anchors)
    if best:
        return best
    whole = (row["filename"], row["description"], pages.start)
    return ["\n\n".join(part for part in whole if part).strip()]


def _documents(reading: _Reading) -> tuple[list[str], list[int]]:
    """Cut what was read into the passages the reranker reads.

    An engram gives its pieces closest to the query, cut as for embedding, so
    each carries its title and heading in front. An engram without vectors
    gives its start. An engram with no text gives its title and description.
    A file gives its name and description, and with reading on its chunks
    closest to the query, see :func:`_file_documents`. Pure, so it can run
    in a thread.

    :param reading: what :func:`_read_candidates` read
    :returns: the passages, and for each the position of its candidate
    """
    texts: dict[tuple[str, UUID], list[str]] = {}
    for row in reading.engrams:
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
        if reading.file_text:
            texts[("file", row["id"])] = _file_documents(
                row,
                reading.pages.get(row["id"], _Pages()),
                reading.chunks.get(row["id"], []),
            )
            continue
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


async def _read_best_pieces(
    engram_ids: list[UUID], embedded: tuple[str, list[float]] | None
) -> tuple[dict[UUID, str | None], list[dict]]:
    """Read what each engram's passage is cut from.

    The best piece is the one :func:`_best_anchors` puts first, the same
    choice the reranker reads first. An engram without vectors of the
    question's model has none, and nothing of it is read.

    :param engram_ids: the engrams found
    :param embedded: the model's name and the query's vector, if any
    :returns: each engram's best anchor, and the text of those engrams
    """
    anchors = await _best_anchors(engram_ids, embedded, 1)
    best = {engram_id: found[0] for engram_id, found in anchors.items()}
    if not best:
        return {}, []
    ids = [str(engram_id) for engram_id in best]
    rows = await Engram.raw(
        _ENGRAM_TEXT_SQL, ids, get_settings().embedding.engram_chars, ids
    )
    return best, list(rows)


def _passages(best: dict[UUID, str | None], engrams: list[dict]) -> dict[UUID, str]:
    """Cut each engram's best piece out of its text, as the reranker reads it.

    The piece carries its title and heading in front, see
    :func:`reef.sections.split`. An engram edited since it was embedded may
    no longer have that piece; it gets none. Pure, so it can run in a thread.

    :param best: each engram's best anchor
    :param engrams: the engrams' text, as :func:`_read_best_pieces` read it
    :returns: each engram's passage, at most :data:`PASSAGE_CHARS` long
    """
    passages: dict[UUID, str] = {}
    for row in engrams:
        title, body = row["title"] or "", row["body"] or ""
        pieces = sections.split(title, row["description"] or summary(body), body)
        anchor = best[row["id"]]
        text = next((piece.text for piece in pieces if piece.anchor == anchor), "")
        if text:
            passages[row["id"]] = text[:PASSAGE_CHARS]
    return passages


async def _read_files(
    nearest: dict[UUID, list[str | None]],
) -> tuple[list[dict], dict[UUID, _Pages]]:
    """Read what each file's passage is cut from: its name, description and pages.

    One statement for the files, one for the pages their closest chunks
    need, in a savepoint: on any failure nothing is read, and each file's
    passage is its snippet. A passage is at most :data:`PASSAGE_CHARS`
    long, so no more of the start of a text is read.

    :param nearest: the closest chunks of the files found that have one
    :returns: the files' id, filename and description, and what was read of
        their pages
    """
    if not nearest:
        return [], {}
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        files = await Engram.raw(
            _FILE_TEXT_SQL,
            [str(file_id) for file_id in nearest],
            AttachmentStatus.READY.value,
        )
        pages = await _read_pages(
            {
                file_id: _wanted_pages(anchors[:1])
                for file_id, anchors in nearest.items()
            },
            PASSAGE_CHARS,
        )
    # Broad on purpose: without its text, a file's snippet will do.
    except Exception as error:  # noqa: BLE001
        await savepoint.rollback_to()
        log.warning("search shows files by their snippet (%s)", type(error).__name__)
        return [], {}
    await savepoint.release()
    return list(files), pages


def _file_passages(
    nearest: dict[UUID, list[str | None]],
    files: list[dict],
    pages: dict[UUID, _Pages],
) -> dict[UUID, str]:
    """Cut each file's closest chunk out of what was read, as the reranker reads it.

    The chunk carries the filename, the description and its page in front,
    see :func:`_file_chunks`. A file read again since it was embedded may no
    longer have that chunk; it gets none. Pure, so it can run in a thread.

    :param nearest: each file's closest chunks, best first
    :param files: the files' id, filename and description
    :param pages: what :func:`_read_pages` read of them
    :returns: each file's passage, at most :data:`PASSAGE_CHARS` long
    """
    passages: dict[UUID, str] = {}
    for row in files:
        texts = _file_chunks(
            row, pages.get(row["id"], _Pages()), nearest[row["id"]][:1]
        )
        # A file without text ends its only chunk in a blank line.
        text = texts[0].rstrip() if texts else ""
        if text:
            passages[row["id"]] = text[:PASSAGE_CHARS]
    return passages


async def _chosen_rows(
    query: str,
    chosen: list[tuple[str, UUID]],
    file_text: bool,
    nearest: dict[UUID, list[str | None]],
) -> list[dict]:
    """Read the result rows of the hits the search chose, in their order.

    They pass through the same joins as a keyword hit, so row security
    decides once more what is shown.

    :param query: the search text
    :param chosen: kind and id of each hit, best first
    :param file_text: whether reading is on. A file hit then names the page
        its passage comes from: its closest chunk's page, None for the
        whole-file chunk, and for a file without vectors its best page by
        words.
    :param nearest: each file's closest chunks, best first
    :returns: the rows
    """
    kinds = [kind for kind, _ in chosen]
    ids = [str(row_id) for _, row_id in chosen]
    if not file_text:
        return await Engram.raw(
            _QUERY_SQL + _CHOSEN_SQL + _CHOSEN_RESULTS_SQL, query, query, kinds, ids
        )
    closest = [kind == "file" and row_id in nearest for kind, row_id in chosen]
    pages = [
        filechunks.page_of(nearest[row_id][0]) if has_chunk else None
        for (_, row_id), has_chunk in zip(chosen, closest, strict=True)
    ]
    return await Engram.raw(
        _QUERY_SQL + _CHOSEN_PAGES_SQL + _CHOSEN_PAGE_RESULTS_SQL,
        query,
        query,
        kinds,
        ids,
        pages,
        closest,
    )


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


async def _large_enough(space_ids: list) -> bool:
    """Say whether the memory searched is large enough to rerank.

    Counts the engrams the caller can see, in the one space searched if there
    is one. Row security does the counting, so nobody else's engrams count.

    :param space_ids: the one space searched, or an empty list
    :returns: True when there are at least ``REEF_RERANK_MIN_ENGRAMS`` engrams
    """
    least = get_settings().rerank.min_engrams
    if least == 0:
        return True
    rows = await Engram.raw(
        _COUNT_SQL + (_SPACE_FILTER_WHERE if space_ids else ""), *space_ids
    )
    return rows[0]["n"] >= least


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


async def search_engrams(
    principal: Principal,
    query: str,
    space: str | None = None,
    limit: int = 10,
    *,
    log: bool = False,
    semantic: bool = True,
    rerank: bool = True,
    passages: bool = False,
) -> list[dict]:
    """Search every engram and described file the caller can see, best match first.

    **By words.** ``websearch_to_tsquery`` parses the query, so plain
    words, quoted phrases, and ``-exclusions`` all work and malformed input
    cannot raise. An engram need not hold every word: engrams that do come
    first, then engrams holding some of them. A query with an exclusion is
    the exception and stays strict. See :data:`_QUERY_SQL`.
    Files match on filename and description, and with reading on
    (``REEF_FILE_TEXT``) on any of their pages; a file counts once, by its
    best match. Only files whose bytes landed (status ``ready``) appear,
    because a hit the reader cannot open reads as reef losing their file.

    **By meaning.** Where there is an embedding service, the query is
    embedded and the engrams closest to it are merged in, see :func:`fuse`.
    An engram can then be found that shares no word with the query, so a
    search with vectors rarely comes back empty. With reading on, files are
    ranked by meaning too, each by its closest chunk, so a photo is found by
    the meaning of its description. With it off, files are found by words
    only.

    **Read again.** Where there is a reranker, and the memory searched
    holds at least ``REEF_RERANK_MIN_ENGRAMS`` engrams, the first
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
    :param space: restrict to one space by its alias; all spaces when None
    :param limit: maximum results, clamped to a sane ceiling
    :param log: record the search in the person's search log. Set by the
        tools a person's assistant calls, not by scripts. See
        :mod:`reef.searchlog`, which records nothing unless switched on.
    :param semantic: merge in the ranking by meaning when there is one.
        False measures keyword search alone.
    :param rerank: let the reranker reorder the first results when there
        is one. False measures the search without it.
    :param passages: give each hit a ``passage`` too: its piece or chunk
        closest to the query, as the reranker reads it, cut to
        :data:`PASSAGE_CHARS` characters. It uses the query's vector the
        search already has, so the query is still embedded once. A hit
        without vectors, or a search without them, gets its snippet; so
        does every file with reading off.
    :returns: one dict per hit — engrams carry path and title, files key,
        filename and ``page``; both carry the space alias, kind, and a
        snippet. ``page`` always says where the file's passage comes from:
        the page of its closest chunk, None when that is the whole-file
        chunk, and for a file without vectors its best page by words, else
        None. A file's snippet comes from that page.
    :raises AccessDenied: when ``space`` names no space of the caller's
    """
    # Postgres text cannot hold a NUL byte, and one in a parameter fails the
    # whole statement rather than matching nothing.
    query = query.replace("\x00", "").strip()
    if not query:
        return []
    settings = get_settings()
    limit = max(1, min(limit, settings.search.max_results))
    exact = _EXACT.search(query) is not None
    file_text = settings.reading.enabled
    embedded = await embedding.embed_question(query) if semantic and not exact else None
    reread = rerank and not exact and reranking.wanted()
    reading = _Reading(candidates=[])
    async with transaction_scope():
        aliases = await alias_map(principal)
        ready = AttachmentStatus.READY.value
        space_filter = ""
        space_ids: list = []
        if space is not None:
            target = await resolve_space(principal, space)
            space_filter = _SPACE_FILTER
            space_ids = [target.id]
        hits_sql = _QUERY_SQL + (_PAGE_HITS_SQL if file_text else _HITS_SQL).format(
            engram_filter=space_filter, file_filter=space_filter
        )
        if reread:
            reread = await _large_enough(space_ids)
        closest = await _closest(embedded, space_ids, file_text) if embedded else []
        if not closest and not reread:
            # Keyword search, in one statement, as it always was.
            rows = await Engram.raw(
                hits_sql + (_PAGE_RESULTS_SQL if file_text else _RESULTS_SQL),
                query,
                query,
                *space_ids,
                ready,
                *space_ids,
                limit,
            )
            await _record(log, principal, query, rows)
            # Nothing was ranked by meaning, so no engram has a best piece
            # and no file a closest chunk.
            return [
                _row_payload(row, aliases, {} if passages else None) for row in rows
            ]
        if closest:
            model_id, vector = embedded
            keyword = await Engram.raw(
                hits_sql + _RANKING_SQL,
                query,
                query,
                *space_ids,
                ready,
                *space_ids,
                max(limit, settings.search.fusion_depth),
                len(query.split()) <= settings.search.sure_words,
                model_id,
                len(vector),
            )
            ranking = fuse(
                [(row["kind"], row["id"], row["sure"]) for row in keyword], closest
            )
        else:
            keyword = await Engram.raw(
                hits_sql + _KEYWORD_RANKING_SQL,
                query,
                query,
                *space_ids,
                ready,
                *space_ids,
                max(limit, settings.rerank.candidates),
            )
            ranking = [(row["kind"], row["id"]) for row in keyword]
        if reread and len(ranking) > 1:
            reading = await _read_candidates(
                ranking[: get_settings().rerank.candidates], embedded, file_text
            )
    if reading.candidates:
        # In a thread: cutting long engrams would hold up every other request.
        documents, owners = await asyncio.to_thread(_documents, reading)
        scores = await reranking.rerank(query, documents) if documents else None
        if scores is not None:
            ranking = reorder(ranking, len(reading.candidates), owners, scores)
    chosen = ranking[:limit]
    best: dict[UUID, str | None] = {}
    texts: list[dict] = []
    files: list[dict] = []
    pages: dict[UUID, _Pages] = {}
    async with transaction_scope():
        await arm(principal)
        # Without a vector in sight, no file has a closest chunk.
        nearest = (
            await _best_chunks(
                [row_id for kind, row_id in chosen if kind == "file"], embedded, 1
            )
            if file_text and closest
            else {}
        )
        rows = await _chosen_rows(query, chosen, file_text, nearest) if chosen else []
        await _record(log, principal, query, rows)
        if passages:
            best, texts = await _read_best_pieces(
                [row["id"] for row in rows if row["kind"] == "page"], embedded
            )
            files, pages = await _read_files(
                {
                    row["id"]: nearest[row["id"]]
                    for row in rows
                    if row["kind"] == "file" and row["id"] in nearest
                }
            )
    # In a thread, like the reranker's passages: cutting long engrams takes time.
    found = await asyncio.to_thread(_passages, best, texts) if texts else {}
    if files:
        found |= await asyncio.to_thread(_file_passages, nearest, files, pages)
    return [_row_payload(row, aliases, found if passages else None) for row in rows]
