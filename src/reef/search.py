"""Full-text search across the caller's pages and files, under RLS.

Postgres FTS, no embeddings yet: the spec's escalation path is
index-plus-selective-read, and this is the selective part. The query runs
in the same armed transaction as every other read, so the row-level
policies scope it — a search can only ever rank content the caller could
have read anyway, and a forgotten filter returns nothing rather than
somebody else's memories.

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

_RESULTS_SQL = f"""
    SELECT
        hits.kind,
        hits.cove_id,
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
    ORDER BY hits.every_word DESC, hits.rank DESC, hits.at DESC
"""

_COVE_FILTER = " AND cove_id = {}"

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


async def search_pages(
    principal: Principal,
    query: str,
    cove: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search every page and described file the caller can see, best match first.

    ``websearch_to_tsquery`` parses the query, so plain words, quoted
    phrases, and ``-exclusions`` all work and malformed input cannot raise.
    A page need not hold every word: pages that do come first, then pages
    holding some of them. A query with an exclusion is the exception and
    stays strict. See :data:`_QUERY_SQL`.
    Files match on filename and description; only files whose bytes landed
    (status ``ready``) appear, because a hit the reader cannot open reads
    as reef losing their file.

    :param principal: the authenticated person
    :param query: words to search for
    :param cove: restrict to one cove by its alias; all coves when None
    :param limit: maximum results, clamped to a sane ceiling
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
    sql = (
        _QUERY_SQL
        + _HITS_SQL.format(page_filter=cove_filter, file_filter=cove_filter)
        + _RESULTS_SQL
    )
    rows = await Page.raw(sql, query, query, *cove_ids, ready, *cove_ids, limit)
    return [_row_payload(row, aliases) for row in rows]
