"""Full-text search across the caller's pages, under RLS.

Postgres FTS, no embeddings: the spec's escalation path is
index-plus-selective-read, and this is the selective part. The query runs
in the same armed transaction as every other read, so the row-level
policies scope it — a search can only ever rank pages the caller could
have read anyway, and a forgotten filter returns nothing rather than
somebody else's memories.
"""

from rif.access import Principal, alias_map, resolve_space
from rif.models import Page

#: Snippets bold their matches as Markdown, the format every reader here speaks.
_HEADLINE_OPTIONS = "StartSel=**, StopSel=**, MaxWords=25, MinWords=8"

#: Title matches count as headline hits, body matches as supporting text.
_VECTOR = (
    "setweight(to_tsvector('english', coalesce(title, '')), 'A') "
    "|| setweight(to_tsvector('english', body), 'B')"
)

_SEARCH_SQL = f"""
    SELECT
        space_id,
        path,
        title,
        ts_headline(
            'english', body, websearch_to_tsquery('english', {{}}),
            '{_HEADLINE_OPTIONS}'
        ) AS snippet,
        ts_rank({_VECTOR}, websearch_to_tsquery('english', {{}})) AS rank
    FROM pages
    WHERE {_VECTOR} @@ websearch_to_tsquery('english', {{}})
"""

_ORDER_SQL = " ORDER BY rank DESC, updated_at DESC LIMIT {}"

_MAX_RESULTS = 50


async def search_pages(
    principal: Principal,
    query: str,
    space: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search every page the caller can see, best match first.

    ``websearch_to_tsquery`` parses the query, so plain words, quoted
    phrases, and ``-exclusions`` all work and malformed input cannot raise.

    :param principal: the authenticated person
    :param query: words to search for
    :param space: restrict to one space by its alias; all spaces when None
    :param limit: maximum results, clamped to a sane ceiling
    :returns: one dict per hit — space alias, path, title, snippet
    :raises AccessDenied: when ``space`` names no space of the caller's
    """
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(limit, _MAX_RESULTS))
    aliases = await alias_map(principal)
    if space is not None:
        target = await resolve_space(principal, space)
        rows = await Page.raw(
            _SEARCH_SQL + " AND space_id = {}" + _ORDER_SQL,
            query,
            query,
            query,
            target.id,
            limit,
        )
    else:
        rows = await Page.raw(_SEARCH_SQL + _ORDER_SQL, query, query, query, limit)
    return [
        {
            "space": aliases[row["space_id"]],
            "path": row["path"],
            "title": row["title"],
            "snippet": row["snippet"],
        }
        for row in rows
    ]
