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


def space_alias(space: Space) -> str:
    """Return the name a space goes by at the tool boundary.

    The principal's own personal space is always addressed as ``personal``;
    every shared space is addressed by its slug. Piccolo stores the kind as
    the enum's string value, so the comparison is against ``.value`` rather
    than the member.

    :param space: the space to name
    :returns: ``personal`` or the space's slug
    """
    return "personal" if space.kind == SpaceKind.PERSONAL.value else space.slug


async def resolve_space(principal: Principal, alias: str) -> Space:
    """Resolve a space name for a principal, arming RLS as a side effect.

    ``personal`` resolves through ownership, not just membership, so
    malformed membership rows cannot hand someone another person's space.
    Any other name is a shared-space slug, resolved through membership. The
    denial message is identical for a missing slug and a slug the principal
    is not a member of, so probing cannot reveal which spaces exist.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :raises AccessDenied: if no such space is reachable by this principal
    :returns: the resolved space
    """
    await arm(principal)
    query = Space.objects().where(
        Space.id.is_in(
            Membership.select(Membership.space_id).where(
                Membership.person_id == principal.person_id
            )
        )
    )
    if alias == "personal":
        query = query.where(
            Space.kind == SpaceKind.PERSONAL.value,
            Space.owner_person_id == principal.person_id,
        )
    else:
        query = query.where(Space.kind == SpaceKind.SHARED.value, Space.slug == alias)
    space = await query.first()
    if space is None:
        raise AccessDenied(f"no space {alias!r} for {principal.email}")
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
