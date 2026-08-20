"""Index-first retrieval, plus the whole-corpus bulk path.

Both entry points assume they run inside :func:`reef.db.transaction_scope`;
:func:`reef.access.accessible_coves` arms RLS before either reads anything.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from reef.access import Principal, accessible_coves, alias_map
from reef.coves import display_names
from reef.models import Attachment, AttachmentStatus, Page, Revision


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


_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
_INLINE_CODE_RE = re.compile(r"`+[^`]*`+")


def _page_references(body: str, source_alias: str) -> list[dict[str, str]]:
    """Extract distinct wiki-link targets from prose in a page body.

    References use the operating protocol's ``[[page.md]]`` and
    ``[[cove:page.md]]`` forms. Fenced and inline code are ignored: those
    commonly contain examples of the syntax and must not turn into graph
    edges. A target's existence and visibility are checked later, once the
    complete accessible index is known.

    :param body: markdown page body
    :param source_alias: alias used to resolve same-cove references
    :returns: ordered, de-duplicated ``cove`` / ``path`` target pairs
    """
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    fence: str | None = None

    for line in body.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is not None:
            continue

        prose = _INLINE_CODE_RE.sub("", line)
        for match in _WIKI_LINK_RE.finditer(prose):
            # A pipe is a conventional optional display label. It isn't part
            # of reef's documented form, but accepting it here is harmless and
            # avoids treating the label as part of a page path.
            target = match.group(1).split("|", 1)[0].strip()
            if ":" in target:
                alias, path = (part.strip() for part in target.split(":", 1))
            else:
                alias, path = source_alias, target
            # Fragments identify a section of the same target page; the
            # page-level index and cove graph intentionally stop at the page.
            path = path.split("#", 1)[0]
            key = (alias, path)
            if not alias or not path or key in seen:
                continue
            seen.add(key)
            references.append({"cove": alias, "path": path})
    return references


def _summary(body: str) -> str:
    """Return the page's one-line description: its first prose line.

    The page style mandates a short summary as the opening paragraph, so the
    first non-heading line is the curated description, not an arbitrary
    excerpt.

    :param body: the page's markdown body
    :returns: the first prose line, trimmed to 200 characters
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:200]
    return ""


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
    pages = await Page.objects().where(Page.cove_id.is_in(cove_ids))
    attachments = await Attachment.objects().where(
        Attachment.cove_id.is_in(cove_ids),
        Attachment.status == AttachmentStatus.READY.value,
    )
    editors = await latest_editors([page.id for page in pages])

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
    visible_pages = {(alias_by_cove_id[page.cove_id], page.path) for page in pages}
    page_path_by_id = {page.id: page.path for page in pages}
    for page in sorted(pages, key=lambda p: p.path):
        source_alias = alias_by_cove_id[page.cove_id]
        references = [
            reference
            for reference in _page_references(page.body, source_alias)
            if (reference["cove"], reference["path"]) in visible_pages
        ]
        by_cove[page.cove_id].pages.append(
            {
                "path": page.path,
                "title": page.title,
                "tags": list(page.tags),
                "description": _summary(page.body),
                "updated": page.updated_at.isoformat(),
                "size": len(page.body),
                "version": page.version,
                "last_editor": editors.get(page.id),
                "references": references,
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
