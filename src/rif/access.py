from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from rif.models import Membership, Space, SpaceKind

_ALIASES = {"personal": SpaceKind.PERSONAL, "household": SpaceKind.HOUSEHOLD}


class AccessDenied(Exception):
    """Raised whenever a principal may not reach the requested space."""


@dataclass(frozen=True)
class Principal:
    """An authenticated person, as established by the transport layer."""

    person_id: UUID
    email: str


async def _set_rls_principal(session: AsyncSession, principal: Principal) -> None:
    """Bind the RLS principal for the current transaction.

    After this, every content-table query in the transaction is filtered by
    Postgres itself; a forgotten application-level filter fails closed.

    :param session: database session
    :param principal: the authenticated person
    """
    await session.execute(
        text("SELECT set_config('app.person_id', :pid, true)"),
        {"pid": str(principal.person_id)},
    )


async def resolve_space(session: AsyncSession, principal: Principal, alias: str) -> Space:
    """Resolve a space alias for a principal, arming RLS as a side effect.

    Personal aliases resolve through ownership, not just membership, so
    malformed membership rows cannot hand someone another person's space.

    :param session: database session
    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :raises AccessDenied: if the alias is unknown or resolves to no unique space
    :returns: the resolved space
    """
    kind = _ALIASES.get(alias)
    if kind is None:
        raise AccessDenied(f"unknown space alias: {alias!r}")
    await _set_rls_principal(session, principal)

    stmt = (
        select(Space)
        .join(Membership, Membership.space_id == Space.id)
        .where(Membership.person_id == principal.person_id, Space.kind == kind)
    )
    if kind is SpaceKind.PERSONAL:
        stmt = stmt.where(Space.owner_person_id == principal.person_id)
    spaces = (await session.scalars(stmt)).all()
    if len(spaces) != 1:
        raise AccessDenied(f"no unique {alias} space for {principal.email}")
    return spaces[0]


async def accessible_spaces(session: AsyncSession, principal: Principal) -> list[Space]:
    """Return every space the principal is a member of, arming RLS.

    :param session: database session
    :param principal: the authenticated person
    :returns: spaces, personal first
    """
    await _set_rls_principal(session, principal)
    stmt = (
        select(Space)
        .join(Membership, Membership.space_id == Space.id)
        .where(Membership.person_id == principal.person_id)
        .order_by(Space.kind)
    )
    return list((await session.scalars(stmt)).all())
