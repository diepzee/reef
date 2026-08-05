from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rif.access import Principal, resolve_space
from rif.models import Page, Revision, Space

PROTECTED_PREFIX = "meta/"


class SectionNotFound(Exception):
    """Raised when a surgical edit cannot find the text it must replace."""


class VersionConflict(Exception):
    """Raised when expected_version does not match the current page version."""


class ProtectedPath(Exception):
    """Raised when a generic write targets the protected meta/ namespace."""


async def list_pages(session: AsyncSession, principal: Principal, alias: str) -> list[Page]:
    """List every page in one of the principal's spaces.

    :param session: database session
    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :returns: pages ordered by path
    """
    space = await resolve_space(session, principal, alias)
    return list((await session.scalars(
        select(Page).where(Page.space_id == space.id).order_by(Page.path))).all())


async def get_page(
    session: AsyncSession, principal: Principal, alias: str, path: str
) -> Page | None:
    """Fetch a single page by path.

    :param session: database session
    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :param path: page path, for example ``house.md``
    :returns: the page, or None if absent from that space
    """
    space = await resolve_space(session, principal, alias)
    return await session.scalar(
        select(Page).where(Page.space_id == space.id, Page.path == path))


async def save_page(
    session: AsyncSession,
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

    :param session: database session
    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :param path: page path
    :param body: full markdown body
    :param message: why this write happened, stored on the revision
    :param title: human-readable title; defaults to the path stem on creation
    :param tags: page tags; unchanged on update when omitted
    :param expected_version: optimistic lock; mismatch raises VersionConflict
    :param allow_protected: permit writes under ``meta/`` (dedicated tools only)
    :raises ProtectedPath: for meta/ paths without allow_protected
    :raises VersionConflict: when expected_version is stale
    :returns: the saved page
    """
    if path.startswith(PROTECTED_PREFIX) and not allow_protected:
        raise ProtectedPath(
            f"{path!r} is protected; protocol and persona change only through "
            "their dedicated tool")
    space = await resolve_space(session, principal, alias)
    page = await session.scalar(
        select(Page).where(Page.space_id == space.id, Page.path == path)
        .with_for_update())
    if page is None:
        if expected_version not in (None, 0):
            raise VersionConflict(f"{path!r} does not exist yet")
        page = Page(space_id=space.id, path=path,
                    title=title or path.removesuffix(".md"),
                    tags=tags or [], body=body, version=1)
        session.add(page)
        await session.flush()
    else:
        if expected_version is not None and page.version != expected_version:
            raise VersionConflict(
                f"{path!r} is at version {page.version}, expected {expected_version}")
        page.body = body
        page.version += 1
        if title is not None:
            page.title = title
        if tags is not None:
            page.tags = tags

    session.add(Revision(page_id=page.id, path=page.path, title=page.title,
                         tags=list(page.tags), body=body, message=message,
                         author_id=principal.person_id))
    await session.execute(
        update(Space).where(Space.id == space.id).values(version=Space.version + 1))
    await session.flush()
    return page


async def edit_section(
    session: AsyncSession,
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

    :param session: database session
    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :param path: page path
    :param old_text: exact text to replace; must occur exactly once
    :param new_text: replacement text
    :param message: why this write happened
    :param expected_version: optimistic lock passed through to save_page
    :param allow_protected: permit edits under ``meta/``
    :raises SectionNotFound: if the page is missing or the span absent/ambiguous
    :returns: the saved page
    """
    page = await get_page(session, principal, alias, path)
    if page is None:
        raise SectionNotFound(f"no page at {path!r}")
    occurrences = page.body.count(old_text)
    if occurrences != 1:
        raise SectionNotFound(
            f"expected exactly one occurrence in {path!r}, found {occurrences}")
    return await save_page(
        session, principal, alias, path, page.body.replace(old_text, new_text),
        message=message,
        expected_version=expected_version if expected_version is not None else page.version,
        allow_protected=allow_protected)
