"""Full-text search across the caller's pages and files, under RLS.

Postgres FTS, no embeddings: the spec's escalation path is
index-plus-selective-read, and this is the selective part. The query runs
in the same armed transaction as every other read, so the row-level
policies scope it — a search can only ever rank content the caller could
have read anyway, and a forgotten filter returns nothing rather than
somebody else's memories.
"""

from reef.access import Principal, alias_map, resolve_space
from reef.models import AttachmentStatus, Page

#: Snippets bold their matches as Markdown, the format every reader here speaks.
_HEADLINE_OPTIONS = "StartSel=**, StopSel=**, MaxWords=25, MinWords=8"

#: Title matches count as headline hits, body matches as supporting text.
_PAGE_VECTOR = (
    "setweight(to_tsvector('english', coalesce(title, '')), 'A') "
    "|| setweight(to_tsvector('english', body), 'B')"
)

#: Filenames are short identifiers, descriptions the substance — same split.
_FILE_VECTOR = (
    "setweight(to_tsvector('english', coalesce(filename, '')), 'A') "
    "|| setweight(to_tsvector('english', description), 'B')"
)

_PAGES_SQL = f"""
    SELECT
        'page' AS kind,
        space_id,
        path,
        title,
        NULL AS key,
        NULL AS filename,
        ts_headline(
            'english', body, websearch_to_tsquery('english', {{}}),
            '{_HEADLINE_OPTIONS}'
        ) AS snippet,
        ts_rank({_PAGE_VECTOR}, websearch_to_tsquery('english', {{}})) AS rank,
        updated_at AS at
    FROM pages
    WHERE {_PAGE_VECTOR} @@ websearch_to_tsquery('english', {{}})
"""

_FILES_SQL = f"""
    SELECT
        'file' AS kind,
        space_id,
        NULL AS path,
        NULL AS title,
        object_key AS key,
        filename,
        ts_headline(
            'english', description, websearch_to_tsquery('english', {{}}),
            '{_HEADLINE_OPTIONS}'
        ) AS snippet,
        ts_rank({_FILE_VECTOR}, websearch_to_tsquery('english', {{}})) AS rank,
        created_at AS at
    FROM attachments
    WHERE {_FILE_VECTOR} @@ websearch_to_tsquery('english', {{}})
      AND status = {{}}
"""

_ORDER_SQL = " ORDER BY rank DESC, at DESC LIMIT {}"

_SPACE_FILTER = " AND space_id = {}"

_MAX_RESULTS = 50


def _row_payload(row: dict, aliases: dict) -> dict:
    """Shape one result row for the tool payload.

    :param row: a row from the union query
    :param aliases: the caller's space-id-to-alias map
    :returns: the fields a reader needs, keyed by kind
    """
    common = {
        "space": aliases[row["space_id"]],
        "kind": row["kind"],
        "snippet": row["snippet"],
    }
    if row["kind"] == "file":
        return {**common, "key": row["key"], "filename": row["filename"]}
    return {**common, "path": row["path"], "title": row["title"]}


async def search_pages(
    principal: Principal,
    query: str,
    space: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search every page and described file the caller can see, best match first.

    ``websearch_to_tsquery`` parses the query, so plain words, quoted
    phrases, and ``-exclusions`` all work and malformed input cannot raise.
    Files match on filename and description; only files whose bytes landed
    (status ``ready``) appear, because a hit the reader cannot open reads
    as reef losing their file.

    :param principal: the authenticated person
    :param query: words to search for
    :param space: restrict to one space by its alias; all spaces when None
    :param limit: maximum results, clamped to a sane ceiling
    :returns: one dict per hit — pages carry path and title, files key and
        filename; both carry the space alias, kind, and a snippet
    :raises AccessDenied: when ``space`` names no space of the caller's
    """
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(limit, _MAX_RESULTS))
    aliases = await alias_map(principal)
    ready = AttachmentStatus.READY.value
    if space is not None:
        target = await resolve_space(principal, space)
        sql = (
            _PAGES_SQL
            + _SPACE_FILTER
            + " UNION ALL "
            + _FILES_SQL
            + _SPACE_FILTER
            + _ORDER_SQL
        )
        rows = await Page.raw(
            sql,
            query,
            query,
            query,
            target.id,
            query,
            query,
            query,
            ready,
            target.id,
            limit,
        )
    else:
        sql = _PAGES_SQL + " UNION ALL " + _FILES_SQL + _ORDER_SQL
        rows = await Page.raw(
            sql, query, query, query, query, query, query, ready, limit
        )
    return [_row_payload(row, aliases) for row in rows]
