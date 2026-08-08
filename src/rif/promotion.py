"""Two-step promotion: prepare a share, then confirm it against a nonce.

Runs inside :func:`rif.db.transaction_scope`; the reads and writes here go
through :mod:`rif.pages`, which arms RLS.
"""

from datetime import timedelta
from uuid import UUID

from rif.access import Principal, resolve_space
from rif.models import Page, Promotion, utc_now
from rif.pages import get_page, save_page

NONCE_TTL = timedelta(minutes=10)


class PromotionError(Exception):
    """Raised when a promotion cannot proceed; the message says why."""


async def prepare_promotion(
    principal: Principal,
    path: str,
    *,
    section: str | None = None,
    dest_path: str | None = None,
) -> dict:
    """Stage a share and return the nonce plus the exact disclosure.

    Whole page: the disclosure is the full body and the destination defaults
    to the same path. Section: ``section`` is the exact text to extract (it
    must occur exactly once) and ``dest_path`` names the new page it becomes —
    the rest of the source page never leaves the personal space.

    The disclosure is what the assistant must show the user before confirming:
    the exact content that will become readable by the other household member,
    permanently.

    :param principal: the authenticated person
    :param path: page path in the personal space
    :param section: exact span to extract; None shares the whole page
    :param dest_path: name of the new household page; required with section
    :raises PromotionError: if the page is missing, the section is absent or
        ambiguous, or a section share names no destination
    :returns: nonce, disclosure text, and destination
    """
    page = await get_page(principal, "personal", path)
    if page is None:
        raise PromotionError(f"no personal page at {path!r}")
    if section is not None:
        if dest_path is None:
            raise PromotionError(
                "a section share needs a dest_path: the extracted section "
                "becomes its own page, and its name is a deliberate choice"
            )
        occurrences = page.body.count(section)
        if occurrences != 1:
            raise PromotionError(
                f"the section text must appear exactly once in {path!r}, "
                f"found {occurrences}"
            )
    dest = await resolve_space(principal, "household")
    staged = Promotion(
        person_id=principal.person_id,
        source_page_id=page.id,
        source_version=page.version,
        dest_space_id=dest.id,
        dest_path=dest_path or path,
        section_text=section,
    )
    await staged.save()
    return {
        "nonce": str(staged.id),
        "dest_path": staged.dest_path,
        "disclosure": section if section is not None else page.body,
        "warning": "Sharing is permanent; there is no un-sharing.",
    }


async def confirm_promotion(principal: Principal, nonce: str) -> dict:
    """Execute a staged promotion: copy to household, stub the original.

    Validates ownership, expiry, source-unchanged, and destination-absent.
    A consumed nonce reports success idempotently, so a transport retry can
    never copy the stub over the promoted content.

    :param principal: the authenticated person
    :param nonce: the id returned by prepare_promotion
    :raises PromotionError: on any failed validation
    :returns: outcome, with already_done=True on an idempotent retry
    """
    await resolve_space(principal, "personal")
    staged = (
        await Promotion.objects().where(Promotion.id == UUID(nonce)).lock_rows().first()
    )
    if staged is None or staged.person_id != principal.person_id:
        raise PromotionError("unknown promotion nonce")
    if staged.consumed_at is not None:
        return {"promoted": True, "dest_path": staged.dest_path, "already_done": True}
    now = utc_now()
    if now - staged.created_at > NONCE_TTL:
        raise PromotionError("promotion expired; prepare it again")

    source = await Page.objects().where(Page.id == staged.source_page_id).first()
    if source is None or source.version != staged.source_version:
        raise PromotionError("the page changed since it was prepared; prepare again")
    if await get_page(principal, "household", staged.dest_path) is not None:
        raise PromotionError(
            f"{staged.dest_path!r} already exists in the household space; "
            "merge through normal edits instead"
        )

    if staged.section_text is not None:
        await save_page(
            principal,
            "household",
            staged.dest_path,
            staged.section_text,
            message=f"section shared from personal by {principal.email}",
            title=staged.dest_path.removesuffix(".md"),
        )
        marker = (
            f"*(section moved to the household space — see `{staged.dest_path}` there)*"
        )
        await save_page(
            principal,
            "personal",
            source.path,
            source.body.replace(staged.section_text, marker),
            message=f"section extracted to household as {staged.dest_path}",
            title=source.title,
            tags=list(source.tags),
        )
    else:
        await save_page(
            principal,
            "household",
            staged.dest_path,
            source.body,
            message=f"promoted from personal by {principal.email}",
            title=source.title,
            tags=list(source.tags),
        )
        await save_page(
            principal,
            "personal",
            staged.dest_path,
            f"# {source.title}\n\nMoved to the household space; "
            f"see `{staged.dest_path}` there.",
            message="stubbed after promotion",
            title=source.title,
        )
    staged.consumed_at = now
    await staged.save()
    return {"promoted": True, "dest_path": staged.dest_path, "already_done": False}
