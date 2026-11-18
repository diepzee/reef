"""The search log: what a person asked, and which pages came back.

**Why it exists.** To measure search on real questions. A search followed,
in the same session, by a read of a page the search did not return is a
miss. Enough of those say where search fails, at no cost to anybody.

**What is recorded.** The query text, the ids of the pages returned in rank
order, a label for the session, and the time. Not the snippets, not the
page text, and not the files returned.

**Whose it is.** One person's. A query is what somebody asked their own
memory, so the log is private to them: row security enforces that, and
nothing here reads across people. Across people this module only counts.

**How long.** :data:`RETENTION_DAYS` days. :func:`purge` deletes older rows
for everybody, through a database function that takes no argument and
returns a number. A person's account deletion takes their rows with it.

**Off by default.** Nothing is recorded unless ``REEF_SEARCH_LOG`` is set.
The privacy page promises that people are told before this takes effect.

It is not the usage log. A page that appears in a search result was not
used, and nothing here feeds activation. See :mod:`reef.usage`.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

from uuid import UUID, uuid4

from reef.access import Principal, arm
from reef.config import get_settings
from reef.models import SearchLog
from reef.rls import SEARCH_LOG_DAYS, SEARCH_LOG_MAX_QUERY
from reef.usage import current_session_id

#: How many days a logged search is kept.
RETENTION_DAYS = SEARCH_LOG_DAYS


async def record(principal: Principal, query: str, page_ids: list[UUID]) -> None:
    """Record one search, if logging is switched on.

    The statement cannot be refused for a legitimate caller: the only rule
    is that the row is the caller's own, and the text is cut to fit first.
    So recording does not put the search itself at risk.

    :param principal: the person who searched
    :param query: the search text, already stripped and not empty
    :param page_ids: the pages returned, best first
    """
    if not get_settings().search_log:
        return
    await arm(principal)
    await SearchLog.raw(
        "INSERT INTO search_log (id, person_id, query, page_ids, session_id) "
        "VALUES ({}, {}, {}, {}, {})",
        uuid4(),
        principal.person_id,
        query[:SEARCH_LOG_MAX_QUERY],
        page_ids,
        current_session_id(),
    )


async def purge() -> int:
    """Delete every logged search older than :data:`RETENTION_DAYS` days.

    For everybody, without reading a row. Needs no principal.

    :returns: how many rows were deleted
    """
    rows = await SearchLog.raw("SELECT reef_purge_search_log() AS removed")
    return rows[0]["removed"]


async def misses(principal: Principal) -> list[dict]:
    """List the person's own searches that were followed by a read they missed.

    A miss is a page the person opened in the same session, within the hour
    after a search, that the search had not returned. Only the person's own
    rows are read: row security hides everybody else's.

    :param principal: the person
    :returns: one dict per miss with the query, the page id and the time,
        newest first
    """
    await arm(principal)
    return await SearchLog.raw(
        "SELECT s.query, a.page_id, s.searched_at "
        "FROM search_log s JOIN page_access a "
        "ON a.person_id = s.person_id AND a.session_id = s.session_id "
        "AND a.via = 'read' AND a.accessed_at >= s.searched_at "
        "AND a.accessed_at < s.searched_at + interval '1 hour' "
        "WHERE NOT (a.page_id = ANY (s.page_ids)) "
        "ORDER BY s.searched_at DESC, a.accessed_at"
    )
