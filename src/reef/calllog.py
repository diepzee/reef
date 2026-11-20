"""The call log: which method was called, and why.

**Why it exists.** So a person can see what was done in their name and the
reason their assistant gave. The reason is the assistant's own account of
why it called, in a sentence.

**What is recorded.** The method, the reason (cut to
:data:`~reef.rls.CALL_LOG_MAX_REASON` characters), who called (``assistant``,
``person`` or ``system``), how long it took, how many things it returned,
the outcome, a label for the session and the time. Not the arguments, not
engram text, and not the results.

**Whose it is.** One person's. Row security lets a person read their own
rows and nobody else's, operators included. Nothing here reads across people.

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
from uuid import uuid4

from reef.access import Principal, arm
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.models import CallLog
from reef.rls import CALL_LOG_DAYS, CALL_LOG_MAX_REASON

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


async def purge() -> int:
    """Delete every logged call older than :data:`RETENTION_DAYS` days.

    For everybody, without reading a row. Needs no principal.

    :returns: how many rows were deleted
    """
    rows = await CallLog.raw("SELECT reef_purge_call_log() AS removed")
    return rows[0]["removed"]
