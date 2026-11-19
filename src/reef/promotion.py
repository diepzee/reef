"""Two-step promotion: prepare a share, then confirm it against a nonce.

Runs inside :func:`reef.db.transaction_scope`; the reads and writes here go
through :mod:`reef.engrams`, which arms RLS.
"""

from datetime import timedelta
from uuid import UUID

from reef.access import (
    AccessDenied,
    Principal,
    ReadOnlyMembership,
    alias_map,
    resolve_space,
    resolve_writable_space,
)
from reef.engrams import get_engram, save_engram
from reef.models import Engram, Promotion, Space, utc_now
from reef.spaces import member_names

NONCE_TTL = timedelta(minutes=10)


class PromotionError(Exception):
    """Raised when a promotion cannot proceed; the message says why.

    The message is for the older tools. The ``code`` is for the methods,
    which map it to a plain message of their own.
    """

    def __init__(self, message: str, code: str = "share_refused") -> None:
        """Build the error.

        :param message: what went wrong, in words
        :param code: a stable code naming the refusal
        """
        super().__init__(message)
        self.code = code


def _access_code(error: AccessDenied) -> str:
    """Name an access refusal: a read-only membership, or an unknown space.

    :param error: what the access layer raised
    :returns: ``read_only`` or ``unknown_space``
    """
    return "read_only" if isinstance(error, ReadOnlyMembership) else "unknown_space"


async def prepare_promotion(
    principal: Principal,
    path: str,
    dest_space: str,
    *,
    section: str | None = None,
    dest_path: str | None = None,
) -> dict:
    """Stage a share and return the nonce plus the exact disclosure.

    Whole engram: the disclosure is the full body and the destination defaults
    to the same path. Section: ``section`` is the exact text to extract (it
    must occur exactly once) and ``dest_path`` names the new engram it becomes —
    the rest of the source engram never leaves the personal space.

    The disclosure is what the assistant must show the user before confirming:
    the exact content that will become readable by every current and future
    member of the destination space, permanently.

    :param principal: the authenticated person
    :param path: engram path in the personal space
    :param dest_space: destination shared-space slug, from load_index
    :param section: exact span to extract; None shares the whole engram
    :param dest_path: name of the new engram in the destination; required with
        section
    :raises PromotionError: if the destination is not a shared space the
        principal belongs to, the engram is missing, the section is absent or
        ambiguous, a section share names no destination, or the destination
        already has an engram at that path
    :returns: nonce, disclosure text, destination, and its members
    """
    if dest_space == "personal":
        raise PromotionError(
            "sharing moves content out of the personal space; pick a shared "
            "space from load_index as the destination",
            "share_to_personal",
        )
    try:
        dest = await resolve_writable_space(principal, dest_space)
    except AccessDenied as exc:
        raise PromotionError(str(exc), _access_code(exc)) from exc
    engram = await get_engram(principal, "personal", path)
    if engram is None:
        raise PromotionError(f"no personal engram at {path!r}", "not_found")
    if section is not None:
        if dest_path is None:
            raise PromotionError(
                "a section share needs a dest_path: the extracted section "
                "becomes its own engram, and its name is a deliberate choice",
                "share_needs_path",
            )
        occurrences = engram.body.count(section)
        if occurrences != 1:
            raise PromotionError(
                f"the section text must appear exactly once in {path!r}, "
                f"found {occurrences}",
                "passage_not_found" if occurrences == 0 else "passage_ambiguous",
            )
    # Checked again at confirm time, since the path may be taken in between.
    # Checked here too, so the person is never asked about a share that
    # cannot happen.
    await _refuse_taken_path(principal, dest_space, dest_path or path)
    staged = Promotion(
        person_id=principal.person_id,
        source_engram_id=engram.id,
        source_version=engram.version,
        dest_space_id=dest.id,
        dest_path=dest_path or path,
        section_text=section,
    )
    await staged.save()
    members = await member_names(dest.id)
    return {
        "nonce": str(staged.id),
        "dest_cove": dest_space,
        "dest_path": staged.dest_path,
        "members": members,
        "disclosure": section if section is not None else engram.body,
        "warning": (
            f"Sharing is permanent; there is no un-sharing. Everyone in "
            f"{dest_space!r} — {', '.join(members)} — and anyone invited "
            "later can read this forever."
        ),
    }


async def _refuse_taken_path(principal: Principal, alias: str, path: str) -> None:
    """Refuse a share whose destination path already holds an engram.

    A share never overwrites: the shared text belongs at a free path.

    :param principal: the authenticated person
    :param alias: the person's name for the destination space
    :param path: the destination path
    :raises PromotionError: ``share_target_exists`` when the path is taken
    """
    if await get_engram(principal, alias, path) is not None:
        raise PromotionError(
            f"{path!r} already exists in the {alias} space; "
            "merge through normal edits instead",
            "share_target_exists",
        )


async def confirm_promotion(principal: Principal, nonce: str, *, channel: str) -> dict:
    """Execute a staged promotion: copy to the staged space, stub the original.

    Validates ownership, expiry, source-unchanged, continued membership in
    the destination, and destination-absent. A consumed nonce reports success
    idempotently, so a transport retry can never copy the stub over the
    promoted content.

    :param principal: the authenticated person
    :param nonce: the id returned by prepare_promotion
    :param channel: who confirmed the share, kept on every revision it
        writes: ``assistant`` over MCP, ``person`` on the website
    :raises PromotionError: on any failed validation
    :returns: outcome, with already_done=True on an idempotent retry
    """
    await resolve_space(principal, "personal")
    staged = (
        await Promotion.objects().where(Promotion.id == UUID(nonce)).lock_rows().first()
    )
    if staged is None or staged.person_id != principal.person_id:
        raise PromotionError("unknown promotion nonce", "stale_token")
    aliases = await alias_map(principal)
    dest = await Space.objects().where(Space.id == staged.dest_space_id).first()
    # The caller's own name for the destination. A space has no single name
    # any more, and the one staged at prepare time is the one this person
    # used then -- read it fresh in case they have renamed it since.
    dest_alias = aliases.get(staged.dest_space_id) if dest else None
    # A nonce outlives the membership that justified it. Once spaces carry a
    # policy, losing that membership makes the destination invisible rather
    # than merely unauthorised, so this is the recheck: without it the code
    # below dereferences None and the caller gets an AttributeError where it
    # should get a refusal.
    if dest is None or dest_alias is None:
        raise PromotionError(
            "you are no longer a member of that space", "unknown_space"
        )
    if staged.consumed_at is not None:
        return {
            "promoted": True,
            "dest_cove": dest_alias,
            "dest_path": staged.dest_path,
            "already_done": True,
        }
    now = utc_now()
    if now - staged.created_at > NONCE_TTL:
        raise PromotionError("promotion expired; prepare it again", "stale_token")

    source = await Engram.objects().where(Engram.id == staged.source_engram_id).first()
    if source is None or source.version != staged.source_version:
        raise PromotionError(
            "the engram changed since it was prepared; prepare again", "stale_token"
        )
    try:
        await resolve_writable_space(principal, dest_alias)
    except AccessDenied as exc:
        raise PromotionError(str(exc), _access_code(exc)) from exc
    await _refuse_taken_path(principal, dest_alias, staged.dest_path)

    if staged.section_text is not None:
        await save_engram(
            principal,
            dest_alias,
            staged.dest_path,
            staged.section_text,
            message=f"section shared from personal by {principal.email}",
            # This is the sanctioned copy: the user has been shown this exact
            # text and who will read it, and has agreed. The guard in
            # save_engram exists to force writes through here, so here is the
            # one place it must not fire.
            allow_private_copy=True,
            title=staged.dest_path.removesuffix(".md"),
            channel=channel,
        )
        marker = (
            f"*(section moved to the {dest_alias} space — see "
            f"`{staged.dest_path}` there)*"
        )
        await save_engram(
            principal,
            "personal",
            source.path,
            source.body.replace(staged.section_text, marker),
            message=f"section extracted to {dest_alias} as {staged.dest_path}",
            title=source.title,
            tags=list(source.tags),
            channel=channel,
        )
    else:
        await save_engram(
            principal,
            dest_alias,
            staged.dest_path,
            source.body,
            message=f"promoted from personal by {principal.email}",
            allow_private_copy=True,
            title=source.title,
            tags=list(source.tags),
            channel=channel,
        )
        # The stub replaces the *source* engram, whatever the destination is
        # named: it is what stays behind in the personal space. Writing it to
        # dest_path instead left the source unstubbed and destroyed any
        # unrelated personal engram of that name. expected_version pins the
        # source that was just re-checked, so nothing is overwritten blind.
        await save_engram(
            principal,
            "personal",
            source.path,
            f"# {source.title}\n\nMoved to the {dest_alias} space; "
            f"see `{staged.dest_path}` there.",
            message="stubbed after promotion",
            title=source.title,
            expected_version=source.version,
            channel=channel,
        )
    staged.consumed_at = now
    await staged.save()
    return {
        "promoted": True,
        "dest_cove": dest_alias,
        "dest_path": staged.dest_path,
        "already_done": False,
    }
