"""Page reads and versioned writes.

Every function here assumes it runs inside :func:`rif.db.transaction_scope`
and arms RLS through :func:`rif.access.resolve_space` before touching
content. There is no session parameter to thread: Piccolo binds queries to
the ambient transaction, and an unarmed one returns nothing.
"""

import re

from rif import audit
from rif.access import Principal, resolve_space
from rif.models import Attachment, Page, Revision, Space

PROTECTED_PREFIX = "meta/"

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
    :raises ProtectedPath: for meta/ paths without allow_protected
    :raises InvalidPath: when a new page's path cannot be normalized
    :raises VersionConflict: when expected_version is stale
    :returns: the saved page
    """
    _refuse_protected(path, allow_protected)
    space = await resolve_space(principal, alias)
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
    space = await resolve_space(principal, alias)
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
