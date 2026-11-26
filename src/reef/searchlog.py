"""The search log: what a person asked, and which engrams came back.

**Why it exists.** To measure search on real questions. A search followed,
in the same session, by a read of an engram the search did not return is a
miss. Enough of those say where search fails, at no cost to anybody.

**What is recorded.** The query text, the ids of the engrams returned in rank
order, a label for the session, and the time. Not the snippets, not the
engram text, and not the files returned.

**Whose it is.** One person's. A query is what somebody asked their own
memory, so the log is private to them: row security enforces that, and
nothing here reads across people. Across people this module only counts.

**Totals across everybody.** :func:`totals` returns only how many searches
and how many of them missed at least one read. Whole UTC days, from 7 to 89,
ending at the start of today, with the partially retained oldest day left out.
Flagged test accounts are left out. No query, person, engram, session or time
leaves the function.

**How long.** :data:`RETENTION_DAYS` days. :func:`purge` deletes older rows
for everybody, through a database function that takes no argument and
returns a number. A person's account deletion takes their rows with it.

**Off by default.** Nothing is recorded unless ``REEF_SEARCH_LOG`` is set.
The privacy page promises that people are told before this takes effect.

**A search stays a read.** The tools that search are marked read-only for
the assistant, and they still are: a logged search changes nothing the
person or their assistant can see in the memory.

It is not the usage log. An engram that appears in a search result was not
used, and nothing here feeds activation. See :mod:`reef.usage`.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from reef.access import Principal, arm
from reef.config import get_settings
from reef.db import DB
from reef.models import SearchLog
from reef.rls import (
    SEARCH_LOG_DAYS,
    SEARCH_LOG_MAX_QUERY,
    SEARCH_LOG_TOTALS_MAX_DAYS,
    SEARCH_LOG_TOTALS_MIN_DAYS,
)
from reef.usage import current_session_id

log = logging.getLogger("reef.searchlog")

#: How many days a logged search is kept.
RETENTION_DAYS = SEARCH_LOG_DAYS


async def record(principal: Principal, query: str, engram_ids: list[UUID]) -> None:
    """Record one search, if logging is switched on.

    Recording must never be what makes a search fail. No refusal is expected
    for a legitimate caller: the only rule is that the row is the caller's
    own, and the text is cut to fit first. But a failed statement aborts the
    whole transaction, and the person would get an error instead of their
    results. So the insert runs inside a savepoint. If it fails, the
    savepoint is rolled back, the class of the error is logged, and the
    search goes on. The query text is never logged.

    :param principal: the person who searched
    :param query: the search text, already stripped and not empty
    :param engram_ids: the engrams returned, best first
    """
    if not get_settings().search.log:
        return
    await arm(principal)
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        await SearchLog.raw(
            "INSERT INTO search_log (id, person_id, query, engram_ids, session_id) "
            "VALUES ({}, {}, {}, {}, {})",
            uuid4(),
            principal.person_id,
            query[:SEARCH_LOG_MAX_QUERY],
            engram_ids,
            current_session_id(),
        )
    # Broad on purpose: whatever went wrong, the search must still answer.
    except Exception as error:  # noqa: BLE001
        await savepoint.rollback_to()
        log.warning("search log: a search was not recorded (%s)", type(error).__name__)
    else:
        await savepoint.release()


async def purge() -> int:
    """Delete every logged search older than :data:`RETENTION_DAYS` days.

    For everybody, without reading a row. Needs no principal.

    :returns: how many rows were deleted
    """
    rows = await SearchLog.raw("SELECT reef_purge_search_log() AS removed")
    return rows[0]["removed"]


@dataclass(frozen=True)
class Total:
    """How many searches and missed searches there were, across everybody."""

    searches: int
    """How many searches fell in the period."""

    misses: int
    """How many of those searches missed at least one read."""


async def totals(days: int) -> Total:
    """Count searches and missed searches without reading anybody's log.

    From the start of the day ``days`` days ago to the start of today, in
    UTC. The database function reads both private logs and returns only
    two numbers. Each search counts once, even if several reads qualify
    under :func:`misses`' rule. Reads at or after the start of today are
    excluded. A search in the last hour of a day may be counted as not
    missed: this is the price of whole days, so today's reads cannot change
    yesterday's count. Needs no principal.

    :param days: how many whole days to count, from
        :data:`~reef.rls.SEARCH_LOG_TOTALS_MIN_DAYS` to
        :data:`~reef.rls.SEARCH_LOG_TOTALS_MAX_DAYS`
    :returns: searches and searches that missed, including zeroes
    :raises ValueError: when ``days`` is out of that range
    """
    if not SEARCH_LOG_TOTALS_MIN_DAYS <= days <= SEARCH_LOG_TOTALS_MAX_DAYS:
        raise ValueError(
            f"search log totals take {SEARCH_LOG_TOTALS_MIN_DAYS} to {SEARCH_LOG_TOTALS_MAX_DAYS} days"
        )
    [row] = await SearchLog.raw(
        "SELECT searches, misses FROM reef_search_log_totals({})", days
    )
    return Total(**row)


async def misses(principal: Principal) -> list[dict]:
    """List the person's own searches that were followed by a read they missed.

    A miss is an engram the person opened in the same session, within the hour
    after a search, that the search had not returned. Only the person's own
    rows are read: row security hides everybody else's.

    A search or a read without a session label is left out. The web
    interface and scripts have none, and without one there is no telling
    which read followed which search.

    :param principal: the person
    :returns: one dict per miss with the query, the engram id and the time,
        newest first
    """
    await arm(principal)
    return await SearchLog.raw(
        "SELECT s.query, a.engram_id, s.searched_at "
        "FROM search_log s JOIN engram_access a "
        "ON a.person_id = s.person_id AND a.session_id = s.session_id "
        "AND a.via = 'read' AND a.accessed_at >= s.searched_at "
        "AND a.accessed_at < s.searched_at + interval '1 hour' "
        "WHERE NOT (a.engram_id = ANY (s.engram_ids)) "
        "ORDER BY s.searched_at DESC, a.accessed_at"
    )
