"""Index-first retrieval, plus the whole-corpus bulk path.

Both entry points assume they run inside :func:`reef.db.transaction_scope`;
:func:`reef.access.accessible_coves` arms RLS before either reads anything.
"""

from dataclasses import dataclass
from uuid import UUID

from reef import embedding
from reef.access import Principal, accessible_coves, alias_map
from reef.coves import display_names
from reef.indexfields import index_fields, resolve_references
from reef.jobs import BACKFILL_INDEX_FIELDS, BACKFILL_LINKS, enqueue
from reef.links import inbound_counts
from reef.models import (
    Attachment,
    AttachmentStatus,
    MemberRole,
    Membership,
    Page,
    Revision,
)


@dataclass
class CoveContext:
    """Everything the principal may see in one cove."""

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
    coves: list[CoveContext]


@dataclass
class CoveIndex:
    """The map of one cove: page metadata and described files, no bodies."""

    alias: str
    version: int
    pages: list[dict]
    attachments: list[dict]


@dataclass
class IndexPayload:
    """The index of everything the principal may see.

    This is the first thing an assistant loads. Each entry carries a one-line
    description — the retrieval surface — so the model can decide which
    entries to fetch with targeted reads.
    """

    version: str
    coves: list[CoveIndex]


#: Everything the index needs from a page. ``body`` is absent on purpose:
#: it is by far the largest column and the index shows none of it.
_INDEX_COLUMNS = (
    Page.id,
    Page.cove_id,
    Page.path,
    Page.title,
    Page.tags,
    Page.updated_at,
    Page.version,
    Page.description,
    Page.size,
    Page.reference_targets,
    Page.indexed_version,
    Page.linked_version,
)


async def _indexed_pages(cove_ids: list[UUID]) -> list[dict]:
    """Return index rows for the pages in ``cove_ids``, without their bodies.

    A page whose stored fields are current costs nothing beyond its row. Any
    other page -- written before the fields existed, or by a writer that did
    not set them -- has its body fetched and the fields derived here, so the
    result is the same either way.

    ``indexed_version`` is left as it was read, so a caller can still tell
    which rows had to be derived.

    :param cove_ids: the coves to read
    :returns: one dict per page, with current ``description``, ``size`` and
        ``reference_targets``
    """
    rows = await Page.select(*_INDEX_COLUMNS).where(Page.cove_id.is_in(cove_ids))
    stale = [row["id"] for row in rows if row["indexed_version"] != row["version"]]
    if stale:
        bodies = {
            row["id"]: row["body"]
            for row in await Page.select(Page.id, Page.body).where(Page.id.is_in(stale))
        }
        for row in rows:
            if row["id"] in bodies:
                row.update(index_fields(bodies[row["id"]]))
    return rows


async def _queue_backfills(principal: Principal, pages: list[dict]) -> None:
    """Queue the work that would bring the principal's old pages up to date.

    This is how old pages get their stored fields and links without
    anything looking across people: the person whose index just had to make
    do without them asks for them to be stored. Asking again while a job
    waits adds nothing.

    A viewer cannot write to a cove, so a backfill run as them would change
    nothing there -- and the index would ask again on every load. Only
    pages in a cove the principal may write to count.

    :param principal: the person whose index was just built
    :param pages: the index rows, as read: ``indexed_version`` and
        ``linked_version`` still say what was stored
    """
    await embedding.request_backfill(principal)
    stale = {
        BACKFILL_INDEX_FIELDS: {
            page["cove_id"]
            for page in pages
            if page["indexed_version"] != page["version"]
        },
        BACKFILL_LINKS: {
            page["cove_id"]
            for page in pages
            if page["linked_version"] != page["version"]
        },
    }
    coves = set().union(*stale.values())
    if not coves:
        return
    writable = {
        row["cove_id"]
        for row in await Membership.select(Membership.cove_id).where(
            Membership.person_id == principal.person_id,
            Membership.role == MemberRole.MEMBER.value,
            Membership.cove_id.is_in(list(coves)),
        )
    }
    for kind, stale_coves in stale.items():
        if stale_coves & writable:
            await enqueue(principal, kind)


async def backfill_index_fields(principal: Principal) -> int:
    """Store current index fields on every page the principal may write.

    Pages saved since the fields were introduced already carry them. This is
    for the ones saved before, which the index otherwise derives from their
    bodies on every call. It changes nothing a reader can observe: no
    version moves, no revision is written.

    Row security decides what "may write" means. An update to a page the
    principal can only read matches no row, so a viewer backfills nothing
    and gets no error.

    :param principal: the person to act as
    :returns: how many pages were updated
    """
    coves = await accessible_coves(principal)
    rows = await Page.select(Page.id, Page.body, Page.version).where(
        Page.cove_id.is_in([cove.id for cove in coves]),
        Page.indexed_version.is_null() | (Page.indexed_version != Page.version),
    )
    updated = 0
    for row in rows:
        fields = index_fields(row["body"])
        changed = (
            await Page.update(
                {
                    Page.description: fields["description"],
                    Page.size: fields["size"],
                    Page.reference_targets: fields["reference_targets"],
                    Page.indexed_version: row["version"],
                }
            )
            # The version guard makes this safe beside a concurrent save:
            # if the page moved on, that save stored fresher fields itself.
            .where(Page.id == row["id"], Page.version == row["version"])
            .returning(Page.id)
        )
        updated += len(changed)
    return updated


async def latest_editors(page_ids: list[UUID]) -> dict[UUID, str | None]:
    """Return each page's newest revision author, as a display name.

    One query for the whole batch: newest-first revisions joined to
    persons, first row per page wins. Pages without revisions, or whose
    author row was erased, map to None.

    :param page_ids: the pages to resolve
    :returns: page id to display name, None where unresolvable
    """
    if not page_ids:
        return {}
    # Two queries rather than one join through ``author_id.display_name``.
    # That join reads ``persons`` directly, and a co-member's row is not
    # readable once that table carries a policy -- every page last touched by
    # somebody else would silently render as "no author". Names come from
    # ``display_names`` instead, which is allowed to answer for people the
    # reader cannot otherwise see.
    rows = (
        await Revision.select(Revision.page_id, Revision.author_id)
        .where(Revision.page_id.is_in(page_ids))
        .order_by(Revision.created_at, ascending=False)
    )
    editors: dict[UUID, str | None] = {pid: None for pid in page_ids}
    latest_author: dict[UUID, UUID] = {}
    seen: set[UUID] = set()
    for row in rows:
        pid = row["page_id"]
        if pid in seen or pid not in editors:
            continue
        seen.add(pid)
        if row["author_id"] is not None:
            latest_author[pid] = row["author_id"]
    names = await display_names(list(latest_author.values()))
    for pid, author_id in latest_author.items():
        editors[pid] = names.get(author_id)
    return editors


async def build_index(principal: Principal) -> IndexPayload:
    """Return the index of every cove the principal can see — no bodies.

    :param principal: the authenticated person
    :returns: the index payload
    """
    coves = await accessible_coves(principal)
    aliases = await alias_map(principal)
    cove_ids = [cove.id for cove in coves]
    pages = await _indexed_pages(cove_ids)
    await _queue_backfills(principal, pages)
    inbound = await inbound_counts([page["id"] for page in pages])
    attachments = await Attachment.objects().where(
        Attachment.cove_id.is_in(cove_ids),
        Attachment.status == AttachmentStatus.READY.value,
    )
    editors = await latest_editors([page["id"] for page in pages])

    # Keyed by cove id: Piccolo Table instances are unhashable, so the row
    # object itself cannot be a dict key the way the SQLAlchemy version did it.
    by_cove = {
        cove.id: CoveIndex(
            alias=aliases[cove.id],
            version=cove.version,
            pages=[],
            attachments=[],
        )
        for cove in coves
    }
    alias_by_cove_id = aliases
    visible_pages = {
        (alias_by_cove_id[page["cove_id"]], page["path"]) for page in pages
    }
    page_path_by_id = {page["id"]: page["path"] for page in pages}
    for page in sorted(pages, key=lambda p: p["path"]):
        source_alias = alias_by_cove_id[page["cove_id"]]
        references = [
            reference
            for reference in resolve_references(page["reference_targets"], source_alias)
            if (reference["cove"], reference["path"]) in visible_pages
        ]
        by_cove[page["cove_id"]].pages.append(
            {
                "path": page["path"],
                "title": page["title"],
                "tags": list(page["tags"]),
                "description": page["description"],
                "updated": page["updated_at"].isoformat(),
                "size": page["size"],
                "version": page["version"],
                "last_editor": editors.get(page["id"]),
                "references": references,
                "inbound": inbound.get(page["id"], 0),
            }
        )
    for attachment in attachments:
        by_cove[attachment.cove_id].attachments.append(
            {
                "key": attachment.object_key,
                "filename": attachment.filename
                or attachment.object_key.rsplit("/", 1)[-1],
                "mime": attachment.mime,
                "size": attachment.byte_size,
                "description": attachment.description,
                "page_path": page_path_by_id.get(attachment.page_id),
            }
        )

    version = (
        ";".join(f"{aliases[cove.id]}={cove.version}" for cove in coves) or "empty"
    )
    return IndexPayload(
        version=f"{principal.person_id}:{version}", coves=list(by_cove.values())
    )


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


async def load_context(principal: Principal, *, char_budget: int) -> ContextPayload:
    """Return every page in every cove the principal can see.

    Bodies are included by priority until the character budget is spent;
    everything else still appears with ``body=None`` so omission is visible,
    never silent.

    :param principal: the authenticated person
    :param char_budget: approximate ceiling on total body characters
    :returns: the assembled context payload
    """
    coves = await accessible_coves(principal)
    aliases = await alias_map(principal)
    cove_ids = [cove.id for cove in coves]
    pages = await Page.objects().where(Page.cove_id.is_in(cove_ids))
    attachments = await Attachment.objects().where(
        Attachment.cove_id.is_in(cove_ids),
        Attachment.status == AttachmentStatus.READY.value,
    )

    spent = 0
    body_by_page: dict[object, str | None] = {}
    page_path_by_id = {page.id: page.path for page in pages}
    for page in sorted(pages, key=_priority):
        if spent + len(page.body) <= char_budget:
            body_by_page[page.id] = page.body
            spent += len(page.body)
        else:
            body_by_page[page.id] = None

    included = sum(1 for body in body_by_page.values() if body is not None)
    truncated = included < len(pages)
    note = (
        f"{len(pages) - included} page(s) exceeded the context budget and are "
        "listed with body=null. Fetch them with read_page when relevant."
        if truncated
        else None
    )

    by_cove = {
        cove.id: CoveContext(
            alias=aliases[cove.id],
            version=cove.version,
            pages=[],
            attachments=[],
        )
        for cove in coves
    }
    for page in sorted(pages, key=lambda p: p.path):
        by_cove[page.cove_id].pages.append(
            {
                "path": page.path,
                "title": page.title,
                "tags": list(page.tags),
                "updated": page.updated_at.isoformat(),
                "size": len(page.body),
                "version": page.version,
                "body": body_by_page[page.id],
            }
        )
    for attachment in attachments:
        by_cove[attachment.cove_id].attachments.append(
            {
                "key": attachment.object_key,
                "filename": attachment.filename
                or attachment.object_key.rsplit("/", 1)[-1],
                "mime": attachment.mime,
                "size": attachment.byte_size,
                "description": attachment.description,
                "page_path": page_path_by_id.get(attachment.page_id),
            }
        )

    version = (
        ";".join(f"{ctx.alias}={ctx.version}" for ctx in by_cove.values()) or "empty"
    )
    return ContextPayload(
        version=version,
        truncated=truncated,
        note=note,
        page_count=len(pages),
        included_count=included,
        coves=list(by_cove.values()),
    )
