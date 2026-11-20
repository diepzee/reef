"""The call log: which method was called, and why.

**Why it exists.** To improve Reef: to learn which tools assistants use,
which they do not, and what they ask for that no tool serves. The reason is
the assistant's own account of why it called, in a sentence. A person sees
their own rows in their export.

**What is recorded.** The method, the reason (cut to
:data:`~reef.rls.CALL_LOG_MAX_REASON` characters), who called (``assistant``,
``person`` or ``system``), how long it took, how many things it returned,
the outcome, a label for the session and the time. Not the arguments, not
engram text, and not the results.

**Calls Reef could not answer.** A call to a retired tool name, or with
arguments that do not fit, is refused before any method runs. The MCP server
records it here too: the name called, the outcome ``retired_tool`` or
``invalid_arguments``, an empty reason, and nothing from the arguments. See
:class:`reef.server._Gate`.

**Whose it is.** One person's. Row security lets a person read their own
rows and nobody else's, operators included.

**Totals across everybody.** :func:`totals` is the one look across people,
and it sees only totals: per method and outcome, how many calls and how many
had no reason. Never a person, a reason, a session, a time, or how many
people. It counts whole days in UTC, at least a week and at most the
retention, ending at the start of today, so no window can be aimed at one
moment. It is how the log serves its purpose without anyone reading it.

**How long.** :data:`RETENTION_DAYS` days. :func:`purge` deletes older rows
for everybody, through a database function that takes no argument and
returns a number. A person's account deletion takes their rows with it.

**Off by default.** Nothing is recorded unless ``REEF_CALL_LOG`` is set.

**The reason is private.** It never reaches Logfire or the app logs:
``reason`` is in :data:`reef.telemetry.SCRUB_PATTERNS`, and no log line
here carries it.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

import logging
from dataclasses import dataclass
from uuid import uuid4

from reef.access import Principal, arm
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.models import CallLog
from reef.rls import CALL_LOG_DAYS, CALL_LOG_MAX_REASON, CALL_LOG_TOTALS_MIN_DAYS

log = logging.getLogger("reef.calllog")

#: How many days a logged call is kept.
RETENTION_DAYS = CALL_LOG_DAYS


async def record(
    principal: Principal,
    *,
    session_id: str | None,
    writer: str,
    method: str,
    reason: str,
    duration_ms: int,
    returned: int,
    outcome: str,
) -> None:
    """Record one call, if logging is switched on.

    Recording must never be what makes a call fail, so every error is
    caught, including from arming and from the savepoint, and only its class
    is logged. The reason is never logged. A transaction the caller already
    aborted makes the insert fail and be skipped, nothing more.

    Call it after the method has returned or raised, outside the method's own
    transactions: it opens its own scope, so the row of a failed call is
    committed instead of rolled back with it.

    :param principal: the person on whose behalf the call was made
    :param session_id: a label for the conversation, or None
    :param writer: ``assistant``, ``person`` or ``system``
    :param method: the name of the method called
    :param reason: why it was called; cut to the stored length
    :param duration_ms: how long the call took, in milliseconds
    :param returned: how many items the call returned
    :param outcome: ``ok``; the code of a refusal, such as ``stale_token``;
        or the class of any other error that ended the call
    """
    if not get_settings().calls.log:
        return
    try:
        # Its own scope: a no-op inside an open transaction, and a
        # transaction of its own outside one, so a call that failed still
        # leaves its row.
        async with transaction_scope():
            await _insert(
                principal,
                session_id=session_id,
                writer=writer,
                method=method,
                reason=reason,
                duration_ms=duration_ms,
                returned=returned,
                outcome=outcome,
            )
    # Broad on purpose: whatever went wrong, the call must still answer.
    except Exception as error:  # noqa: BLE001
        log.warning("call log: a call was not recorded (%s)", type(error).__name__)


async def _insert(
    principal: Principal,
    *,
    session_id: str | None,
    writer: str,
    method: str,
    reason: str,
    duration_ms: int,
    returned: int,
    outcome: str,
) -> None:
    """Insert one row inside a savepoint, so a refusal leaves the transaction usable.

    Any error is raised to :func:`record`, which logs its class. The
    savepoint is rolled back only if it was created.

    :param principal: the person on whose behalf the call was made
    :param session_id: a label for the conversation, or None
    :param writer: ``assistant``, ``person`` or ``system``
    :param method: the name of the method called
    :param reason: why it was called; cut to the stored length
    :param duration_ms: how long the call took, in milliseconds
    :param returned: how many items the call returned
    :param outcome: ``ok``; the code of a refusal, such as ``stale_token``;
        or the class of any other error that ended the call
    """
    await arm(principal)
    savepoint = await DB.current_transaction.get().savepoint()
    try:
        await CallLog.raw(
            "INSERT INTO call_log (id, person_id, session_id, method, reason, "
            "writer, duration_ms, returned, outcome) "
            "VALUES ({}, {}, {}, {}, {}, {}, {}, {}, {})",
            uuid4(),
            principal.person_id,
            session_id,
            method,
            reason[:CALL_LOG_MAX_REASON],
            writer,
            duration_ms,
            returned,
            outcome,
        )
    except Exception:
        await savepoint.rollback_to()
        raise
    await savepoint.release()


@dataclass(frozen=True)
class Total:
    """How the calls to one method that ended one way add up, across everybody."""

    method: str
    """The method, or the retired tool name, that was called."""

    outcome: str
    """How the calls ended: ``ok``, a refusal's code, or an error's class."""

    calls: int
    """How many calls."""

    empty_reasons: int
    """How many of them came without a reason."""


async def totals(days: int) -> list[Total]:
    """Count the calls of the last whole days, for everybody, without reading one.

    From the start of the day ``days`` days ago to the start of today, in
    UTC: today is not counted until it is over. Through a database function
    that returns only these totals, ordered by method and outcome, and that
    refuses a number of days out of range too. Needs no principal.

    :param days: how many whole days to count, from
        :data:`~reef.rls.CALL_LOG_TOTALS_MIN_DAYS` to :data:`RETENTION_DAYS`
    :returns: one total per method and outcome
    :raises ValueError: when ``days`` is out of that range
    """
    if not CALL_LOG_TOTALS_MIN_DAYS <= days <= CALL_LOG_DAYS:
        raise ValueError(
            f"call log totals take {CALL_LOG_TOTALS_MIN_DAYS} to {CALL_LOG_DAYS} days"
        )
    rows = await CallLog.raw(
        "SELECT method, outcome, calls, empty_reasons FROM reef_call_log_totals({})",
        days,
    )
    return [Total(**row) for row in rows]


async def purge() -> int:
    """Delete every logged call older than :data:`RETENTION_DAYS` days.

    For everybody, without reading a row. Needs no principal.

    :returns: how many rows were deleted
    """
    rows = await CallLog.raw("SELECT reef_purge_call_log() AS removed")
    return rows[0]["removed"]
