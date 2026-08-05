from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rif.access import Principal, accessible_spaces
from rif.models import Attachment, AttachmentStatus, Page, SpaceKind

_ALIAS_BY_KIND = {SpaceKind.PERSONAL: "personal", SpaceKind.HOUSEHOLD: "household"}


@dataclass
class SpaceContext:
    """Everything the principal may see in one space."""

    alias: str
    version: int
    pages: list[dict]
    attachments: list[dict]


@dataclass
class ContextPayload:
    """The whole corpus a principal may see, plus loading metadata.

    ``page_count`` and ``included_count`` let a client detect host-side
    truncation of the serialized result: if the pages it can see do not add up,
    the payload was cut in transit.
    """

    version: str
    truncated: bool
    note: str | None
    page_count: int
    included_count: int
    spaces: list[SpaceContext]


def _priority(page: Page) -> tuple:
    """Return the inclusion sort key: meta first, core-tagged second, small third.

    :param page: the page to rank
    :returns: sort key, ascending
    """
    return (
        0 if page.path.startswith("meta/") else 1,
        0 if "core" in page.tags else 1,
        len(page.body),
    )


async def load_context(
    session: AsyncSession, principal: Principal, *, char_budget: int
) -> ContextPayload:
    """Return every page in every space the principal can see.

    Bodies are included by priority until the character budget is spent;
    everything else still appears with ``body=None`` so omission is visible,
    never silent.

    :param session: database session
    :param principal: the authenticated person
    :param char_budget: approximate ceiling on total body characters
    :returns: the assembled context payload
    """
    spaces = await accessible_spaces(session, principal)
    space_ids = [space.id for space in spaces]
    pages = list((await session.scalars(
        select(Page).where(Page.space_id.in_(space_ids)))).all())
    attachments = list((await session.scalars(
        select(Attachment).where(Attachment.space_id.in_(space_ids),
                                 Attachment.status == AttachmentStatus.READY))).all())

    spent = 0
    body_by_page: dict[Page, str | None] = {}
    for page in sorted(pages, key=_priority):
        if spent + len(page.body) <= char_budget:
            body_by_page[page] = page.body
            spent += len(page.body)
        else:
            body_by_page[page] = None

    included = sum(1 for body in body_by_page.values() if body is not None)
    truncated = included < len(pages)
    note = (
        f"{len(pages) - included} page(s) exceeded the context budget and are "
        "listed with body=null. Fetch them with read_page when relevant."
        if truncated else None)

    by_space = {
        space: SpaceContext(alias=_ALIAS_BY_KIND[space.kind], version=space.version,
                            pages=[], attachments=[])
        for space in spaces}
    space_by_id = {space.id: space for space in spaces}
    for page in sorted(pages, key=lambda p: p.path):
        by_space[space_by_id[page.space_id]].pages.append({
            "path": page.path, "title": page.title, "tags": list(page.tags),
            "updated": page.updated_at.isoformat(), "size": len(page.body),
            "version": page.version, "body": body_by_page[page]})
    for attachment in attachments:
        by_space[space_by_id[attachment.space_id]].attachments.append({
            "key": attachment.object_key, "mime": attachment.mime,
            "description": attachment.description})

    version = ";".join(
        f"{space.slug}={space.version}" for space in spaces) or "empty"
    return ContextPayload(
        version=f"{principal.person_id}:{version}", truncated=truncated, note=note,
        page_count=len(pages), included_count=included,
        spaces=list(by_space.values()))
