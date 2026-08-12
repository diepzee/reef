"""Two-step promotion: prepare a share, then confirm it against a nonce.

Runs inside :func:`rif.db.transaction_scope`; the reads and writes here go
through :mod:`rif.pages`, which arms RLS.
"""

from datetime import timedelta
from uuid import UUID

from rif.access import AccessDenied, Principal, resolve_space
from rif.models import Page, Promotion, Space, utc_now
from rif.pages import get_page, save_page
from rif.spaces import member_names

NONCE_TTL = timedelta(minutes=10)


class PromotionError(Exception):
    """Raised when a promotion cannot proceed; the message says why."""


async def prepare_promotion(
    principal: Principal,
    path: str,
    dest_space: str,
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
    the exact content that will become readable by every current and future
    member of the destination space, permanently.

    :param principal: the authenticated person
    :param path: page path in the personal space
    :param dest_space: destination shared-space slug, from list_spaces
    :param section: exact span to extract; None shares the whole page
    :param dest_path: name of the new page in the destination; required with
        section
    :raises PromotionError: if the destination is not a shared space the
        principal belongs to, the page is missing, the section is absent or
        ambiguous, or a section share names no destination
    :returns: nonce, disclosure text, destination, and its members
    """
    if dest_space == "personal":
        raise PromotionError(
            "sharing moves content out of the personal space; pick a shared "
            "space from list_spaces as the destination"
        )
    try:
        dest = await resolve_space(principal, dest_space)
    except AccessDenied as exc:
        raise PromotionError(str(exc)) from exc
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
    staged = Promotion(
        person_id=principal.person_id,
        source_page_id=page.id,
        source_version=page.version,
        dest_space_id=dest.id,
        dest_path=dest_path or path,
        section_text=section,
    )
    await staged.save()
    members = await member_names(dest.id)
    return {
        "nonce": str(staged.id),
        "dest_space": dest_space,
        "dest_path": staged.dest_path,
        "members": members,
        "disclosure": section if section is not None else page.body,
        "warning": (
            f"Sharing is permanent; there is no un-sharing. Everyone in "
            f"{dest_space!r} — {', '.join(members)} — and anyone invited "
            "later can read this forever."
        ),
    }


async def confirm_promotion(principal: Principal, nonce: str) -> dict:
    """Execute a staged promotion: copy to the staged space, stub the original.

    Validates ownership, expiry, source-unchanged, continued membership in
    the destination, and destination-absent. A consumed nonce reports success
    idempotently, so a transport retry can never copy the stub over the
    promoted content.

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
    dest = await Space.objects().where(Space.id == staged.dest_space_id).first()
    # A nonce outlives the membership that justified it. Once spaces carry a
    # policy, losing that membership makes the destination invisible rather
    # than merely unauthorised, so this is the recheck: without it the code
    # below dereferences None and the caller gets an AttributeError where it
    # should get a refusal.
    if dest is None:
        raise PromotionError("you are no longer a member of that cove")
    if staged.consumed_at is not None:
        return {
            "promoted": True,
            "dest_space": dest.slug,
            "dest_path": staged.dest_path,
            "already_done": True,
        }
    now = utc_now()
    if now - staged.created_at > NONCE_TTL:
        raise PromotionError("promotion expired; prepare it again")

    source = await Page.objects().where(Page.id == staged.source_page_id).first()
    if source is None or source.version != staged.source_version:
        raise PromotionError("the page changed since it was prepared; prepare again")
    try:
        await resolve_space(principal, dest.slug)
    except AccessDenied as exc:
        raise PromotionError(str(exc)) from exc
    if await get_page(principal, dest.slug, staged.dest_path) is not None:
        raise PromotionError(
            f"{staged.dest_path!r} already exists in the {dest.slug} space; "
            "merge through normal edits instead"
        )

    if staged.section_text is not None:
        await save_page(
            principal,
            dest.slug,
            staged.dest_path,
            staged.section_text,
            message=f"section shared from personal by {principal.email}",
            title=staged.dest_path.removesuffix(".md"),
        )
        marker = (
            f"*(section moved to the {dest.slug} space — see "
            f"`{staged.dest_path}` there)*"
        )
        await save_page(
            principal,
            "personal",
            source.path,
            source.body.replace(staged.section_text, marker),
            message=f"section extracted to {dest.slug} as {staged.dest_path}",
            title=source.title,
            tags=list(source.tags),
        )
    else:
        await save_page(
            principal,
            dest.slug,
            staged.dest_path,
            source.body,
            message=f"promoted from personal by {principal.email}",
            title=source.title,
            tags=list(source.tags),
        )
        # The stub replaces the *source* page, whatever the destination is
        # named: it is what stays behind in the personal space. Writing it to
        # dest_path instead left the source unstubbed and destroyed any
        # unrelated personal page of that name. expected_version pins the
        # source that was just re-checked, so nothing is overwritten blind.
        await save_page(
            principal,
            "personal",
            source.path,
            f"# {source.title}\n\nMoved to the {dest.slug} space; "
            f"see `{staged.dest_path}` there.",
            message="stubbed after promotion",
            title=source.title,
            expected_version=source.version,
        )
    staged.consumed_at = now
    await staged.save()
    return {
        "promoted": True,
        "dest_space": dest.slug,
        "dest_path": staged.dest_path,
        "already_done": False,
    }
