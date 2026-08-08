"""The accessor: binds the RLS principal and resolves space aliases.

This is the review-critical surface. Everything that reads or writes content
goes through :func:`arm` first, inside a :func:`rif.db.transaction_scope`,
and Postgres does the rest.
"""

from dataclasses import dataclass
from uuid import UUID

from rif.models import Membership, Space, SpaceKind

_ALIASES = {"personal": SpaceKind.PERSONAL, "household": SpaceKind.SHARED}


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


async def resolve_space(principal: Principal, alias: str) -> Space:
    """Resolve a space alias for a principal, arming RLS as a side effect.

    Personal aliases resolve through ownership, not just membership, so
    malformed membership rows cannot hand someone another person's space.

    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :raises AccessDenied: if the alias is unknown or resolves to no unique space
    :returns: the resolved space
    """
    kind = _ALIASES.get(alias)
    if kind is None:
        raise AccessDenied(f"unknown space alias: {alias!r}")
    await arm(principal)

    query = Space.objects().where(
        Space.id.is_in(
            Membership.select(Membership.space_id).where(
                Membership.person_id == principal.person_id
            )
        ),
        Space.kind == kind.value,
    )
    if kind is SpaceKind.PERSONAL:
        query = query.where(Space.owner_person_id == principal.person_id)
    spaces = await query
    if len(spaces) != 1:
        raise AccessDenied(f"no unique {alias} space for {principal.email}")
    return spaces[0]


async def accessible_spaces(principal: Principal) -> list[Space]:
    """Return every space the principal is a member of, arming RLS.

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
