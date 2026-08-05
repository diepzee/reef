from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rif.access import Principal, resolve_space
from rif.models import Page, Promotion
from rif.pages import get_page, save_page

NONCE_TTL = timedelta(minutes=10)


class PromotionError(Exception):
    """Raised when a promotion cannot proceed; the message says why."""


async def prepare_promotion(
    session: AsyncSession, principal: Principal, path: str
) -> dict:
    """Stage a promotion and return the nonce plus a disclosure summary.

    The summary is what the assistant must show the user before confirming:
    the exact content that will become readable by the other household member,
    permanently.

    :param session: database session
    :param principal: the authenticated person
    :param path: page path in the personal space
    :raises PromotionError: if the page does not exist
    :returns: nonce, disclosure text, and destination
    """
    page = await get_page(session, principal, "personal", path)
    if page is None:
        raise PromotionError(f"no personal page at {path!r}")
    staged = Promotion(person_id=principal.person_id, source_page_id=page.id,
                       source_version=page.version, dest_path=path)
    session.add(staged)
    await session.flush()
    return {"nonce": str(staged.id), "dest_path": path,
            "disclosure": page.body,
            "warning": "Promotion is permanent; there is no demotion."}


async def confirm_promotion(
    session: AsyncSession, principal: Principal, nonce: str
) -> dict:
    """Execute a staged promotion: copy to household, stub the original.

    Validates ownership, expiry, source-unchanged, and destination-absent.
    A consumed nonce reports success idempotently, so a transport retry can
    never copy the stub over the promoted content.

    :param session: database session
    :param principal: the authenticated person
    :param nonce: the id returned by prepare_promotion
    :raises PromotionError: on any failed validation
    :returns: outcome, with already_done=True on an idempotent retry
    """
    await resolve_space(session, principal, "personal")
    staged = await session.scalar(
        select(Promotion).where(Promotion.id == UUID(nonce)).with_for_update())
    if staged is None or staged.person_id != principal.person_id:
        raise PromotionError("unknown promotion nonce")
    if staged.consumed_at is not None:
        return {"promoted": True, "dest_path": staged.dest_path, "already_done": True}
    now = datetime.now(UTC)
    if now - staged.created_at.replace(tzinfo=UTC) > NONCE_TTL:
        raise PromotionError("promotion expired; prepare it again")

    source = await session.get(Page, staged.source_page_id)
    if source is None or source.version != staged.source_version:
        raise PromotionError("the page changed since it was prepared; prepare again")
    if await get_page(session, principal, "household", staged.dest_path) is not None:
        raise PromotionError(
            f"{staged.dest_path!r} already exists in the household space; "
            "merge through normal edits instead")

    await save_page(session, principal, "household", staged.dest_path, source.body,
                    message=f"promoted from personal by {principal.email}",
                    title=source.title, tags=list(source.tags))
    await save_page(session, principal, "personal", staged.dest_path,
                    f"# {source.title}\n\nMoved to the household space; "
                    f"see `{staged.dest_path}` there.",
                    message="stubbed after promotion", title=source.title)
    staged.consumed_at = now.replace(tzinfo=None)
    await session.flush()
    return {"promoted": True, "dest_path": staged.dest_path, "already_done": False}
