"""The accessor: binds the RLS principal and resolves space aliases.

This is the review-critical surface. Everything that reads or writes content
goes through :func:`arm` first, inside a :func:`rif.db.transaction_scope`,
and Postgres does the rest.
"""

from dataclasses import dataclass
from uuid import UUID

from rif.models import Membership, Space, SpaceKind


class AccessDenied(Exception):
    """Raised whenever a principal may not reach the requested space."""


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
    await Space.raw(
        "SELECT set_config('app.person_id', {}, true)", str(principal.person_id)
    )


PERSONAL_ALIAS = "personal"
"""The one alias whose meaning is fixed for everybody.

Reserved per person rather than globally: it names *your* private space, and
:func:`resolve_space` resolves it through ownership so no membership row,
however malformed, can point it somewhere else.
"""


async def alias_map(principal: Principal) -> dict[UUID, str]:
    """Return the name this principal addresses each of their coves by.

    Aliases live on the membership, so a cove has no single name: two people
    may call the same cove different things, and two people may each have a
    cove called ``family`` with no relation between them. Everything that
    renders a cove name therefore needs the reader's own mapping rather than
    a property of the row -- which is why the old ``space_alias(space)``,
    a pure function of the space, no longer exists.

    :param principal: the authenticated person
    :returns: space id to the alias this principal uses for it
    """
    await arm(principal)
    rows = await Membership.select(Membership.space_id, Membership.alias).where(
        Membership.person_id == principal.person_id
    )
    return {row["space_id"]: row["alias"] for row in rows}


async def resolve_space(principal: Principal, alias: str) -> Space:
    """Resolve a cove name for a principal, arming RLS as a side effect.

    Every name is looked up on the principal's *own* membership rows, so a
    name means whatever this person decided it means and nothing outside
    their memberships is reachable by guessing.

    ``personal`` is checked twice over: the membership must carry that alias
    *and* the space must be a personal one this principal owns. Alias
    uniqueness is per person, so a cove admitted under that name would
    otherwise shadow the private space in every later call -- the admit path
    refuses it, and this refuses it again on the read side.

    The denial message is identical for a name nobody uses and a name that
    belongs to somebody else, so probing reveals nothing about what exists.

    :param principal: the authenticated person
    :param alias: ``personal`` or one of this principal's cove names
    :raises AccessDenied: if no such space is reachable by this principal
    :returns: the resolved space
    """
    await arm(principal)
    denied = AccessDenied(f"no space {alias!r} for {principal.email}")
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
    query = Space.objects().where(Space.id == membership.space_id)
    if alias == PERSONAL_ALIAS:
        query = query.where(
            Space.kind == SpaceKind.PERSONAL.value,
            Space.owner_person_id == principal.person_id,
        )
    space = await query.first()
    if space is None:
        raise denied
    return space


async def accessible_spaces(principal: Principal) -> list[Space]:
    """Return every space the principal is a member of, arming RLS.

    Ordered by ``Space.kind``, which Piccolo stores as text: ``'personal'``
    sorts before ``'shared'`` lexically, so the personal space always comes
    first.

    :param principal: the authenticated person
    :returns: spaces, personal first
    """
    await arm(principal)
    return (
        await Space.objects()
        .where(
            Space.id.is_in(
                Membership.select(Membership.space_id).where(
                    Membership.person_id == principal.person_id
                )
            )
        )
        .order_by(Space.kind)
    )
