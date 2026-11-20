"""The usage log, and the activation computed from it.

**What is recorded.** Real use only: an engram was opened, or written. An engram
that merely appeared in a search result or in the index was not used, and
recording it would teach the system that whatever it shows is what matters.

**Whose it is.** One person's. The log and the activation are private to
the person they describe, also for engrams in a shared space. Row security
enforces that, and nothing here looks across people.

**Activation.** How readily an engram comes to mind for one person, from how
often and how recently they used it. It is the base-level term of ACT-R,
a model of human memory::

    activation = ln( sum over recent uses of  weight * age ** -0.5 )

Each use adds to it, and each contribution fades with the square root of
its age, so ten uses last month count for about as much as two today. Only
the most recent ``REEF_USAGE_WINDOW`` uses of an engram are counted.

Activation changes as time passes, without any new use. The stored value
is therefore a snapshot, refreshed by a job a little after each use. It is
good for ordering one person's engrams against each other, which is what it
is for. It is not comparable between people.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

import hashlib
from datetime import timedelta
from uuid import UUID, uuid4

from reef.access import Principal, arm
from reef.config import get_settings
from reef.jobs import REFRESH_ACTIVATION, enqueue
from reef.models import AccessKind, EngramAccess

#: How much each kind of use counts. Something a person explicitly asked to
#: have remembered counts three times a routine read or write.
WEIGHTS: dict[AccessKind, float] = {
    AccessKind.READ: 1.0,
    AccessKind.WRITE: 1.0,
    AccessKind.WEB: 1.0,
    AccessKind.REMEMBER: 3.0,
}


def current_session_id() -> str | None:
    """Return a stable label for the current assistant session, if there is one.

    A digest of the transport's session id rather than the id itself: the
    id is what a client presents to continue a session, and there is no
    reason to keep it in a table.

    The digest is cut to 32 characters to keep the column short. That is a
    label for grouping, not a security measure.

    :returns: 32 hexadecimal characters, or None outside an assistant
        session -- the web interface, the worker, a test
    """
    try:
        from fastmcp.server.dependencies import get_context

        raw = get_context().session_id
    except RuntimeError:
        return None
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def record(principal: Principal, engram_id: UUID, via: AccessKind) -> None:
    """Record that the principal used an engram, and ask for a refresh.

    :param principal: the person who used the engram
    :param engram_id: the engram
    :param via: how it was used
    """
    await arm(principal)
    # Recording must never be what makes a read or a write fail. The insert
    # policy refuses a row about an engram the person cannot read, and a
    # refusal would abort the caller's whole transaction -- which can happen
    # to a legitimate caller whose membership is removed between their read
    # and this line. So the statement checks for itself and, when the engram
    # is not readable, inserts nothing. It answers the same way for an engram
    # that does not exist, so it tells the caller nothing either.
    recorded = await EngramAccess.raw(
        "INSERT INTO engram_access (id, person_id, engram_id, via, weight, session_id) "
        "SELECT {}, {}, {}, {}, {}, {} "
        "WHERE EXISTS (SELECT 1 FROM engrams WHERE id = {}) RETURNING id",
        uuid4(),
        principal.person_id,
        engram_id,
        via.value,
        WEIGHTS[via],
        current_session_id(),
        engram_id,
    )
    if recorded:
        await enqueue(
            principal,
            REFRESH_ACTIVATION,
            delay=timedelta(seconds=get_settings().usage.refresh_delay_seconds),
        )


async def refresh_activation(principal: Principal) -> int:
    """Recompute the principal's activation for every engram they have used.

    Replaces what was stored. An engram the person can no longer read drops
    out: row security hides its log rows, so nothing is computed for it.
    Its old value stays where it is, hidden from them by the same rule,
    until the engram or the person is deleted.

    :param principal: the person to recompute for
    :returns: how many engrams now carry an activation
    """
    await arm(principal)
    await EngramAccess.raw(
        "DELETE FROM engram_heat WHERE person_id = {}", principal.person_id
    )
    stored = await EngramAccess.raw(
        "INSERT INTO engram_heat "
        "(id, person_id, engram_id, activation, accesses, last_access) "
        "SELECT gen_random_uuid(), {}, engram_id, "
        "ln(sum(weight * power(greatest("
        "extract(epoch FROM (LOCALTIMESTAMP - accessed_at)) / 3600.0, {}), {})) "
        "FILTER (WHERE recency <= {})), "
        "count(*), max(accessed_at) "
        "FROM (SELECT engram_id, weight, accessed_at, row_number() OVER "
        "(PARTITION BY engram_id ORDER BY accessed_at DESC) AS recency "
        "FROM engram_access WHERE person_id = {}) AS uses "
        "GROUP BY engram_id RETURNING engram_id",
        principal.person_id,
        get_settings().usage.min_age_hours,
        -get_settings().usage.decay,
        get_settings().usage.window,
        principal.person_id,
    )
    return len(stored)


async def activations(principal: Principal) -> dict[UUID, float]:
    """Return the principal's stored activation per engram.

    :param principal: the person whose activation to read
    :returns: engram id to activation; engrams never used are absent
    """
    await arm(principal)
    rows = await EngramAccess.raw(
        "SELECT engram_id, activation FROM engram_heat WHERE person_id = {}",
        principal.person_id,
    )
    return {row["engram_id"]: row["activation"] for row in rows}
