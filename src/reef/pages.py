"""Page reads and versioned writes.

Every function here assumes it runs inside :func:`reef.db.transaction_scope`
and arms RLS through :func:`reef.access.resolve_space` before touching
content. There is no session parameter to thread: Piccolo binds queries to
the ambient transaction, and an unarmed one returns nothing.
"""

import re
from datetime import datetime

from reef import audit
from reef.access import AccessDenied, Principal, resolve_space, resolve_writable_space
from reef.config import get_settings
from reef.leakguard import overlaps
from reef.models import Attachment, Page, Revision, Space, SpaceKind

PROTECTED_PREFIX = "meta/"

PERSONA_PATH = "meta/persona.md"
"""The persona page, and the one personal page exempt from the leak guard.

Defined here rather than in :mod:`reef.protocol`, which re-exports it: that
module imports this one, so the reverse would be a cycle.
"""

#: Characters a normalized page path may contain.
_ALLOWED = re.compile(r"[a-z0-9\-/._]")


class SectionNotFound(Exception):
    """Raised when a surgical edit cannot find the text it must replace."""


class VersionConflict(Exception):
    """Raised when expected_version does not match the current page version."""


class ProtectedPath(Exception):
    """Raised when a generic write targets the protected meta/ namespace."""


class InvalidPath(Exception):
    """Raised when a page path cannot be repaired into a usable one."""


class PrivateContentLeak(Exception):
    """Raised when a cove write carries text copied from the personal space."""


async def _refuse_private_copy(
    principal: Principal,
    alias: str,
    path: str,
    body: str,
    existing: Page | None,
) -> None:
    """Refuse a shared write that copies the caller's own personal pages.

    This is what makes the share ceremony a boundary rather than a
    convention; :mod:`reef.leakguard` explains the threat it answers and,
    just as importantly, what it does not catch.

    The personal pages are read as the caller, under the same policies as
    any other read, so this discloses nothing the caller could not already
    fetch. The persona is skipped: it is seeded from a fixed template, so
    every person's copy shares its wording with every other's, and matching
    against it would refuse writes over text that is not private at all.

    :param principal: the authenticated person
    :param alias: the destination cove
    :param path: the page being written, for the message
    :param body: the body about to be written
    :param existing: the stored page, whose text is exempt
    :raises PrivateContentLeak: when the write introduces private text
    """
    try:
        personal = await resolve_space(principal, "personal")
    except AccessDenied:
        # No personal space, so nothing private to copy out of. Onboarding
        # creates one for everybody, but a principal without one must still
        # be able to write to a cove -- a guard that refuses the write it
        # cannot evaluate would turn a missing row into a lockout.
        return
    private = (
        await Page.select(Page.body)
        .where(Page.space_id == personal.id, Page.path != PERSONA_PATH)
        .output(as_list=True)
    )
    # Re-arm: resolve_space above bound the principal for the personal
    # lookup, and the caller's next statement expects the same principal.
    # It is the same value, so this is belt and braces rather than a fix.
    await resolve_space(principal, alias)
    if not overlaps(body, existing.body if existing else "", private):
        return
    raise PrivateContentLeak(
        f"this would copy text from your personal space into {alias!r} "
        f"({path!r}). Sharing is permanent, so it goes through "
        "prepare_to_share: it shows the user the exact text and who will be "
        "able to read it, and confirm_share performs the move after they "
        "agree. If the wording only coincides, rewrite it in your own words "
        "for this cove."
    )


class PageTooLarge(Exception):
    """Raised when a body exceeds the per-page character ceiling."""


def validate_body(path: str, body: str) -> None:
    """Reject a page body past the per-page ceiling.

    Pages had no size limit at all while files were capped at 25 MB, so the
    cheapest way to bloat a cove was to write prose into it. The cost is not
    storage: :func:`reef.context.build_index` reads every accessible body on
    every call to compute descriptions and references, so one oversized page
    is paid for by every member, in every conversation, forever.

    The ceiling is deliberately above ``context_char_budget`` -- a page
    larger than the whole context budget cannot be loaded in one piece
    anyway, so anything at that size is already past the point of being
    useful memory.

    :param path: the page path, for the message
    :param body: the full markdown body
    :raises PageTooLarge: if the body exceeds ``REEF_PAGE_MAX_CHARS``
    """
    ceiling = get_settings().page_max_chars
    if len(body) > ceiling:
        raise PageTooLarge(
            f"{path!r} is {len(body)} characters, over the {ceiling}-character "
            "page limit; split it into several pages"
        )


def normalize_path(path: str) -> str:
    """Return the path a new page will actually be stored under.

    Mirrors ``frontend/src/pagePath.ts`` so the browser, the CLI, and an
    assistant calling ``write_page`` all name a page the same way. Before
    this existed the ``.md`` rule was enforced only by the web form, and the
    MCP surface happily stored ``notes/NO-EXTENSION`` and ``notes/Spaced
    Out.md`` beside it.

    Anything mechanical is repaired rather than refused -- case, surrounding
    and interior whitespace, and the missing ``.md``. What is left cannot be
    guessed at, so it raises.

    :param path: the path as asked for
    :raises InvalidPath: for an empty or dot segment, a leading slash, a
        character with no sensible repair, or a name that is only the
        extension
    :returns: the normalized path
    """
    cleaned = re.sub(r"\s+", "-", path.strip().lower())
    if not cleaned:
        raise InvalidPath("a page needs a path")
    if not cleaned.endswith(".md"):
        cleaned = f"{cleaned}.md"
    if cleaned.startswith("/"):
        raise InvalidPath(f"{path!r} may not start with '/'")
    segments = cleaned.split("/")
    if any(segment == "" for segment in segments):
        raise InvalidPath(f"{path!r} has an empty folder in it")
    if any(segment in (".", "..") for segment in segments):
        raise InvalidPath(f"{path!r} may not contain '.' or '..' segments")
    offenders = sorted({char for char in cleaned if not _ALLOWED.fullmatch(char)})
    if offenders:
        shown = ", ".join(repr(char) for char in offenders)
        raise InvalidPath(
            f"{path!r} contains {shown}; use letters, digits, '-', '_', '.' and '/'"
        )
    if segments[-1] == ".md":
        raise InvalidPath(f"{path!r} names no page, only an extension")
    return cleaned


def _refuse_protected(path: str, allow_protected: bool) -> None:
    """Raise unless ``path`` may be written by a generic write.

    Checked against both the path as asked for and its normalized form, so
    ``META/Persona`` cannot slip past a check made before lowercasing.

    :param path: the path being written
    :param allow_protected: whether the caller is a dedicated meta/ tool
    :raises ProtectedPath: for a meta/ path without allow_protected
    """
    if path.startswith(PROTECTED_PREFIX) and not allow_protected:
        raise ProtectedPath(
            f"{path!r} is protected; protocol and persona change only through "
            "their dedicated tool"
        )


async def list_pages(principal: Principal, alias: str) -> list[Page]:
    """List every page in one of the principal's spaces.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :returns: pages ordered by path
    """
    space = await resolve_space(principal, alias)
    return await Page.objects().where(Page.space_id == space.id).order_by(Page.path)


async def get_page(principal: Principal, alias: str, path: str) -> Page | None:
    """Fetch a single page by path.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: page path, for example ``house.md``
    :returns: the page, or None if absent from that space
    """
    space = await resolve_space(principal, alias)
    return (
        await Page.objects().where(Page.space_id == space.id, Page.path == path).first()
    )


async def get_page_as_of(
    principal: Principal, alias: str, path: str, as_of: datetime
) -> dict | None:
    """Fetch the state a page had at a moment in the past.

    Each revision snapshots the state its write produced, so the newest
    revision at or before ``as_of`` *is* the page as it stood then. The
    lookup runs under the same armed RLS as a present-day read: history is
    exactly as private as the page it belongs to.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: page path, for example ``house.md``
    :param as_of: the moment to reconstruct; aware datetimes are converted
        to the naive local time the revision timestamps are stored in
    :returns: the page fields at that moment, or None if it did not exist
    """
    if as_of.tzinfo is not None:
        as_of = as_of.astimezone().replace(tzinfo=None)
    page = await get_page(principal, alias, path)
    if page is None:
        return None
    revision = (
        await Revision.objects()
        .where(Revision.page_id == page.id, Revision.created_at <= as_of)
        .order_by(Revision.created_at, ascending=False)
        .first()
    )
    if revision is None:
        return None
    return {
        "path": revision.path,
        "title": revision.title,
        "tags": list(revision.tags),
        "body": revision.body,
        "created": revision.created_at.isoformat(),
    }


async def save_page(
    principal: Principal,
    alias: str,
    path: str,
    body: str,
    *,
    message: str,
    title: str | None = None,
    tags: list[str] | None = None,
    expected_version: int | None = None,
    allow_protected: bool = False,
    allow_private_copy: bool = False,
) -> Page:
    """Create or overwrite a page: snapshot a revision, bump page and space versions.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: page path
    :param body: full markdown body
    :param message: why this write happened, stored on the revision
    :param title: human-readable title; defaults to the path stem on creation
    :param tags: page tags; unchanged on update when omitted
    :param expected_version: optimistic lock; mismatch raises VersionConflict
    :param allow_protected: permit writes under ``meta/`` (dedicated tools only)
    :param allow_private_copy: permit shared text copied from the personal
        space. Only the share ceremony passes this -- it *is* the sanctioned
        copy, having already shown the user the exact disclosure.
    :raises PrivateContentLeak: for personal text written into a cove
    :raises ProtectedPath: for meta/ paths without allow_protected
    :raises InvalidPath: when a new page's path cannot be normalized
    :raises PageTooLarge: for a body over the per-page ceiling
    :raises VersionConflict: when expected_version is stale
    :returns: the saved page
    """
    validate_body(path, body)
    _refuse_protected(path, allow_protected)
    space = await resolve_writable_space(principal, alias)
    # An existing page is addressed by exactly the name it already carries.
    # reef stored arbitrary paths before normalize_path existed, and
    # normalizing unconditionally would not *rename* such a page -- it would
    # write a second one beside it under the tidy name and strand the
    # original. So: old pages keep their names, new pages get good ones, and
    # the mess stops growing without anything already stored breaking.
    page = (
        await Page.objects()
        .where(Page.space_id == space.id, Page.path == path)
        .lock_rows()
        .first()
    )
    if page is None:
        path = normalize_path(path)
        _refuse_protected(path, allow_protected)
        page = (
            await Page.objects()
            .where(Page.space_id == space.id, Page.path == path)
            .lock_rows()
            .first()
        )
    if not allow_private_copy and space.kind == SpaceKind.SHARED.value:
        await _refuse_private_copy(principal, alias, path, body, page)
    if page is None:
        if expected_version not in (None, 0):
            raise VersionConflict(f"{path!r} does not exist yet")
        page = Page(
            space_id=space.id,
            path=path,
            title=title or path.removesuffix(".md"),
            tags=tags or [],
            body=body,
            version=1,
        )
        await page.save()
    else:
        if expected_version is not None and page.version != expected_version:
            raise VersionConflict(
                f"{path!r} is at version {page.version}, expected {expected_version}"
            )
        page.body = body
        page.version += 1
        if title is not None:
            page.title = title
        if tags is not None:
            page.tags = tags
        await page.save()

    await Revision(
        page_id=page.id,
        path=page.path,
        title=page.title,
        tags=list(page.tags),
        body=body,
        message=message,
        author_id=principal.person_id,
    ).save()
    await Space.update({Space.version: Space.version + 1}).where(Space.id == space.id)
    return page


async def edit_section(
    principal: Principal,
    alias: str,
    path: str,
    old_text: str,
    new_text: str,
    *,
    message: str,
    expected_version: int | None = None,
    allow_protected: bool = False,
    allow_private_copy: bool = False,
) -> Page:
    """Replace an exact, unique span of a page, leaving the rest untouched.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: page path
    :param old_text: exact text to replace; must occur exactly once
    :param new_text: replacement text
    :param message: why this write happened
    :param expected_version: optimistic lock passed through to save_page
    :param allow_protected: permit edits under ``meta/``
    :raises SectionNotFound: if the page is missing or the span absent/ambiguous
    :returns: the saved page
    """
    page = await get_page(principal, alias, path)
    if page is None:
        raise SectionNotFound(f"no page at {path!r}")
    occurrences = page.body.count(old_text)
    if occurrences != 1:
        raise SectionNotFound(
            f"expected exactly one occurrence in {path!r}, found {occurrences}"
        )
    return await save_page(
        principal,
        alias,
        path,
        page.body.replace(old_text, new_text),
        message=message,
        expected_version=expected_version
        if expected_version is not None
        else page.version,
        allow_protected=allow_protected,
    )


class PageNotFound(Exception):
    """Raised when a page to be deleted is not in that space."""


async def delete_page(principal: Principal, alias: str, path: str) -> dict:
    """Delete a page and its history, permanently.

    The counterpart to a mistyped path. Until this existed a page could be
    blanked but never removed, so a fat-fingered name was a permanent
    resident of the cove and the only way out was deleting the whole cove.

    Any member may delete, which is the same authority they already have to
    overwrite the page with nothing -- the difference is that this also takes
    the revisions, so it is a genuine loss and the caller is expected to have
    confirmed it. ``meta/`` is refused: the persona and protocol pages are
    part of the machinery, and a cove without them is not a state the rest of
    the code is written to expect.

    Attachments that hung off the page survive it -- ``page_id`` is
    ``ON DELETE SET NULL`` -- because the bytes were uploaded deliberately
    and are reachable in the cove's file list on their own.

    Recorded in the audit trail for the reason ``audit`` gives for cove
    deletion: once the page and its revisions are gone, nothing left in the
    database can say it existed or who ended it.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: the exact path of the page to delete
    :raises ProtectedPath: for a page under ``meta/``
    :raises PageNotFound: if that space has no page at that path
    :returns: the deleted path and how many revisions went with it
    """
    _refuse_protected(path, allow_protected=False)
    space = await resolve_writable_space(principal, alias)
    page = (
        await Page.objects()
        .where(Page.space_id == space.id, Page.path == path)
        .lock_rows()
        .first()
    )
    if page is None:
        raise PageNotFound(f"no page at {path!r}")

    revisions = await Revision.count().where(Revision.page_id == page.id)
    # Explicitly, and before the page: the revision -> page foreign key has no
    # cascade, so leaving this to the database would refuse the delete rather
    # than tidy up after it.
    await Revision.delete().where(Revision.page_id == page.id)
    await Attachment.update({Attachment.page_id: None}).where(
        Attachment.page_id == page.id
    )
    await Page.delete().where(Page.id == page.id)
    await Space.update({Space.version: Space.version + 1}).where(Space.id == space.id)
    audit.record(
        audit.PAGE_DELETED,
        actor=principal.person_id,
        space_id=space.id,
        revisions=revisions,
    )
    return {"deleted": True, "path": path, "revisions": revisions}
