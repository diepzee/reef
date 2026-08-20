"""The accessor: binds the RLS principal and resolves cove aliases.

This is the review-critical surface. Everything that reads or writes content
goes through :func:`arm` first, inside a :func:`reef.db.transaction_scope`,
and Postgres does the rest.
"""

from dataclasses import dataclass
from uuid import UUID

from reef.models import Cove, CoveKind, MemberRole, Membership


class AccessDenied(Exception):
    """Raised whenever a principal may not reach the requested cove."""


class ReadOnlyMembership(AccessDenied):
    """Raised when a viewer membership tries to change a cove's content.

    Postgres already refuses the write — the ``role = 'member'`` predicate
    has guarded every content table since day one — but its refusal is a
    zero-row update or a constraint error, neither of which tells an
    assistant what to say. This raises before any work, with the reason.
    """


@dataclass(frozen=True)
class Principal:
    """An authenticated person, as established by the transport layer."""

    person_id: UUID
    email: str


async def arm(principal: Principal) -> None:
    """Bind the RLS principal for the current transaction.

    After this, every content-table query in the transaction is filtered by
    Postgres itself; a forgotten application-level filter fails closed. The
    third argument to ``set_config`` makes the binding transaction-local, so
    it cannot outlive this request and reach the next borrower of the pooled
    connection.

    :param principal: the authenticated person
    """
    await Cove.raw(
        "SELECT set_config('app.person_id', {}, true)", str(principal.person_id)
    )


PERSONAL_ALIAS = "personal"
"""The one alias whose meaning is fixed for everybody.

Reserved per person rather than globally: it names *your* private cove, and
:func:`resolve_cove` resolves it through ownership so no membership row,
however malformed, can point it somewhere else.
"""


async def alias_map(principal: Principal) -> dict[UUID, str]:
    """Return the name this principal addresses each of their coves by.

    Aliases live on the membership, so a cove has no single name: two people
    may call the same cove different things, and two people may each have a
    cove called ``family`` with no relation between them. Everything that
    renders a cove name therefore needs the reader's own mapping rather than
    a property of the row -- which is why the old ``cove_alias(cove)``,
    a pure function of the cove, no longer exists.

    :param principal: the authenticated person
    :returns: cove id to the alias this principal uses for it
    """
    await arm(principal)
    rows = await Membership.select(Membership.cove_id, Membership.alias).where(
        Membership.person_id == principal.person_id
    )
    return {row["cove_id"]: row["alias"] for row in rows}


async def resolve_cove(principal: Principal, alias: str) -> Cove:
    """Resolve a cove name for a principal, arming RLS as a side effect.

    Every name is looked up on the principal's *own* membership rows, so a
    name means whatever this person decided it means and nothing outside
    their memberships is reachable by guessing.

    ``personal`` is checked twice over: the membership must carry that alias
    *and* the cove must be a personal one this principal owns. Alias
    uniqueness is per person, so a cove admitted under that name would
    otherwise shadow the private cove in every later call -- the admit path
    refuses it, and this refuses it again on the read side.

    The denial message is identical for a name nobody uses and a name that
    belongs to somebody else, so probing reveals nothing about what exists.

    :param principal: the authenticated person
    :param alias: ``personal`` or one of this principal's cove names
    :raises AccessDenied: if no such cove is reachable by this principal
    :returns: the resolved cove
    """
    await arm(principal)
    denied = AccessDenied(f"no cove {alias!r} for {principal.email}")
    membership = (
        await Membership.objects()
        .where(
            Membership.person_id == principal.person_id,
            Membership.alias == alias,
        )
        .first()
    )
    if membership is None:
        raise denied
    query = Cove.objects().where(Cove.id == membership.cove_id)
    if alias == PERSONAL_ALIAS:
        query = query.where(
            Cove.kind == CoveKind.PERSONAL.value,
            Cove.owner_person_id == principal.person_id,
        )
    cove = await query.first()
    if cove is None:
        raise denied
    return cove


async def resolve_writable_cove(principal: Principal, alias: str) -> Cove:
    """Resolve a cove for writing: the same lookup, plus the role gate.

    Every content-write path resolves through here so a viewer gets a
    refusal that names the reason, before any statement runs. The database
    enforces the same rule regardless — this is the message, not the lock.

    :param principal: the authenticated person
    :param alias: ``personal`` or one of this principal's cove names
    :raises AccessDenied: if no such cove is reachable by this principal
    :raises ReadOnlyMembership: if the membership may read but not write
    :returns: the resolved cove
    """
    cove = await resolve_cove(principal, alias)
    membership = (
        await Membership.objects()
        .where(
            Membership.person_id == principal.person_id,
            Membership.cove_id == cove.id,
        )
        .first()
    )
    if membership is not None and membership.role != MemberRole.MEMBER.value:
        raise ReadOnlyMembership(
            f"you are a read-only member of {alias!r}: reading is welcome, "
            "but changing its content is reserved for full members"
        )
    return cove


async def accessible_coves(principal: Principal) -> list[Cove]:
    """Return every cove the principal is a member of, arming RLS.

    Ordered by ``Cove.kind``, which Piccolo stores as text: ``'personal'``
    sorts before ``'shared'`` lexically, so the personal cove always comes
    first.

    :param principal: the authenticated person
    :returns: coves, personal first
    """
    await arm(principal)
    return (
        await Cove.objects()
        .where(
            Cove.id.is_in(
                Membership.select(Membership.cove_id).where(
                    Membership.person_id == principal.person_id
                )
            )
        )
        .order_by(Cove.kind)
    )
