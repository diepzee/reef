"""Index-first retrieval, plus the whole-corpus bulk path.

Both entry points assume they run inside :func:`reef.db.transaction_scope`;
:func:`reef.access.accessible_spaces` arms RLS before either reads anything.
"""

from dataclasses import dataclass
from uuid import UUID

from reef import embedding
from reef.access import Principal, accessible_spaces, alias_map
from reef.indexfields import index_fields, resolve_references
from reef.jobs import BACKFILL_INDEX_FIELDS, BACKFILL_LINKS, enqueue
from reef.links import inbound_counts
from reef.models import (
    Attachment,
    AttachmentStatus,
    Engram,
    MemberRole,
    Membership,
    Revision,
)
from reef.spaces import display_names

# The field names below are the wire format: these payloads reach the
# methods, the MCP tools and the web API as they are, through ``asdict``.


@dataclass
class SpaceContext:
    """Everything the principal may see in one space."""

    alias: str
    version: int
    engrams: list[dict]
    attachments: list[dict]


@dataclass
class ContextPayload:
    """The whole corpus a principal may see, plus loading metadata.

    ``engram_count`` and ``included_count`` let a client detect host-side
    truncation of the serialized result: if the engrams it can see do not add up,
    the payload was cut in transit.
    """

    version: str
    truncated: bool
    note: str | None
    engram_count: int
    included_count: int
    spaces: list[SpaceContext]


@dataclass
class SpaceIndex:
    """The map of one space: engram metadata and described files, no bodies."""

    alias: str
    version: int
    engrams: list[dict]
    attachments: list[dict]


@dataclass
class IndexPayload:
    """The index of everything the principal may see.

    This is the first thing an assistant loads. Each entry carries a one-line
    description — the retrieval surface — so the model can decide which
    entries to fetch with targeted reads.
    """

    version: str
    spaces: list[SpaceIndex]


#: Everything the index needs from an engram. ``body`` is absent on purpose:
#: it is by far the largest column and the index shows none of it.
_INDEX_COLUMNS = (
    Engram.id,
    Engram.space_id,
    Engram.path,
    Engram.title,
    Engram.tags,
    Engram.updated_at,
    Engram.version,
    Engram.description,
    Engram.size,
    Engram.reference_targets,
    Engram.indexed_version,
    Engram.linked_version,
)


async def _indexed_engrams(space_ids: list[UUID]) -> list[dict]:
    """Return index rows for the engrams in ``space_ids``, without their bodies.

    An engram whose stored fields are current costs nothing beyond its row. Any
    other engram -- written before the fields existed, or by a writer that did
    not set them -- has its body fetched and the fields derived here, so the
    result is the same either way.

    ``indexed_version`` is left as it was read, so a caller can still tell
    which rows had to be derived.

    :param space_ids: the spaces to read
    :returns: one dict per engram, with current ``description``, ``size`` and
        ``reference_targets``
    """
    rows = await Engram.select(*_INDEX_COLUMNS).where(Engram.space_id.is_in(space_ids))
    stale = [row["id"] for row in rows if row["indexed_version"] != row["version"]]
    if stale:
        bodies = {
            row["id"]: row["body"]
            for row in await Engram.select(Engram.id, Engram.body).where(
                Engram.id.is_in(stale)
            )
        }
        for row in rows:
            if row["id"] in bodies:
                row.update(index_fields(bodies[row["id"]]))
    return rows


async def _queue_backfills(principal: Principal, engrams: list[dict]) -> None:
    """Queue the work that would bring the principal's old engrams up to date.

    This is how old engrams get their stored fields and links without
    anything looking across people: the person whose index just had to make
    do without them asks for them to be stored. Asking again while a job
    waits adds nothing.

    A viewer cannot write to a space, so a backfill run as them would change
    nothing there -- and the index would ask again on every load. Only
    engrams in a space the principal may write to count.

    :param principal: the person whose index was just built
    :param engrams: the index rows, as read: ``indexed_version`` and
        ``linked_version`` still say what was stored
    """
    await embedding.request_backfill(principal)
    stale = {
        BACKFILL_INDEX_FIELDS: {
            engram["space_id"]
            for engram in engrams
            if engram["indexed_version"] != engram["version"]
        },
        BACKFILL_LINKS: {
            engram["space_id"]
            for engram in engrams
            if engram["linked_version"] != engram["version"]
        },
    }
    spaces = set().union(*stale.values())
    if not spaces:
        return
    writable = {
        row["space_id"]
        for row in await Membership.select(Membership.space_id).where(
            Membership.person_id == principal.person_id,
            Membership.role == MemberRole.MEMBER.value,
            Membership.space_id.is_in(list(spaces)),
        )
    }
    for kind, stale_spaces in stale.items():
        if stale_spaces & writable:
            await enqueue(principal, kind)


async def backfill_index_fields(principal: Principal) -> int:
    """Store current index fields on every engram the principal may write.

    Engrams saved since the fields were introduced already carry them. This is
    for the ones saved before, which the index otherwise derives from their
    bodies on every call. It changes nothing a reader can observe: no
    version moves, no revision is written.

    Row security decides what "may write" means. An update to an engram the
    principal can only read matches no row, so a viewer backfills nothing
    and gets no error.

    :param principal: the person to act as
    :returns: how many engrams were updated
    """
    spaces = await accessible_spaces(principal)
    rows = await Engram.select(Engram.id, Engram.body, Engram.version).where(
        Engram.space_id.is_in([space.id for space in spaces]),
        Engram.indexed_version.is_null() | (Engram.indexed_version != Engram.version),
    )
    updated = 0
    for row in rows:
        fields = index_fields(row["body"])
        changed = (
            await Engram.update(
                {
                    Engram.description: fields["description"],
                    Engram.size: fields["size"],
                    Engram.reference_targets: fields["reference_targets"],
                    Engram.indexed_version: row["version"],
                }
            )
            # The version guard makes this safe beside a concurrent save:
            # if the engram moved on, that save stored fresher fields itself.
            .where(Engram.id == row["id"], Engram.version == row["version"])
            .returning(Engram.id)
        )
        updated += len(changed)
    return updated


async def latest_editors(engram_ids: list[UUID]) -> dict[UUID, str | None]:
    """Return each engram's newest revision author, as a display name.

    One query for the whole batch: newest-first revisions joined to
    persons, first row per engram wins. Engrams without revisions, or whose
    author row was erased, map to None.

    :param engram_ids: the engrams to resolve
    :returns: engram id to display name, None where unresolvable
    """
    if not engram_ids:
        return {}
    # Two queries rather than one join through ``author_id.display_name``.
    # That join reads ``persons`` directly, and a co-member's row is not
    # readable once that table carries a policy -- every engram last touched by
    # somebody else would silently render as "no author". Names come from
    # ``display_names`` instead, which is allowed to answer for people the
    # reader cannot otherwise see.
    rows = (
        await Revision.select(Revision.engram_id, Revision.author_id)
        .where(Revision.engram_id.is_in(engram_ids))
        .order_by(Revision.created_at, ascending=False)
    )
    editors: dict[UUID, str | None] = {pid: None for pid in engram_ids}
    latest_author: dict[UUID, UUID] = {}
    seen: set[UUID] = set()
    for row in rows:
        pid = row["engram_id"]
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
    """Return the index of every space the principal can see — no bodies.

    :param principal: the authenticated person
    :returns: the index payload
    """
    spaces = await accessible_spaces(principal)
    aliases = await alias_map(principal)
    space_ids = [space.id for space in spaces]
    engrams = await _indexed_engrams(space_ids)
    await _queue_backfills(principal, engrams)
    inbound = await inbound_counts([engram["id"] for engram in engrams])
    attachments = await Attachment.objects().where(
        Attachment.space_id.is_in(space_ids),
        Attachment.status == AttachmentStatus.READY.value,
    )
    editors = await latest_editors([engram["id"] for engram in engrams])

    # Keyed by space id: Piccolo Table instances are unhashable, so the row
    # object itself cannot be a dict key the way the SQLAlchemy version did it.
    by_space = {
        space.id: SpaceIndex(
            alias=aliases[space.id],
            version=space.version,
            engrams=[],
            attachments=[],
        )
        for space in spaces
    }
    alias_by_space_id = aliases
    visible_engrams = {
        (alias_by_space_id[engram["space_id"]], engram["path"]) for engram in engrams
    }
    engram_path_by_id = {engram["id"]: engram["path"] for engram in engrams}
    for engram in sorted(engrams, key=lambda p: p["path"]):
        source_alias = alias_by_space_id[engram["space_id"]]
        references = [
            reference
            for reference in resolve_references(
                engram["reference_targets"], source_alias
            )
            if (reference["space"], reference["path"]) in visible_engrams
        ]
        by_space[engram["space_id"]].engrams.append(
            {
                "path": engram["path"],
                "title": engram["title"],
                "tags": list(engram["tags"]),
                "description": engram["description"],
                "updated": engram["updated_at"].isoformat(),
                "size": engram["size"],
                "version": engram["version"],
                "last_editor": editors.get(engram["id"]),
                "references": references,
                "inbound": inbound.get(engram["id"], 0),
            }
        )
    for attachment in attachments:
        by_space[attachment.space_id].attachments.append(
            {
                "key": attachment.object_key,
                "filename": attachment.filename
                or attachment.object_key.rsplit("/", 1)[-1],
                "mime": attachment.mime,
                "size": attachment.byte_size,
                "description": attachment.description,
                "engram_path": engram_path_by_id.get(attachment.engram_id),
            }
        )

    version = (
        ";".join(f"{aliases[space.id]}={space.version}" for space in spaces) or "empty"
    )
    return IndexPayload(
        version=f"{principal.person_id}:{version}", spaces=list(by_space.values())
    )


def _priority(engram: Engram) -> tuple:
    """Return the inclusion sort key: meta first, core-tagged second, small third.

    :param engram: the engram to rank
    :returns: sort key, ascending
    """
    return (
        0 if engram.path.startswith("meta/") else 1,
        0 if "core" in engram.tags else 1,
        len(engram.body),
    )


async def load_context(principal: Principal, *, char_budget: int) -> ContextPayload:
    """Return every engram in every space the principal can see.

    Bodies are included by priority until the character budget is spent;
    everything else still appears with ``body=None`` so omission is visible,
    never silent.

    :param principal: the authenticated person
    :param char_budget: approximate ceiling on total body characters
    :returns: the assembled context payload
    """
    spaces = await accessible_spaces(principal)
    aliases = await alias_map(principal)
    space_ids = [space.id for space in spaces]
    engrams = await Engram.objects().where(Engram.space_id.is_in(space_ids))
    attachments = await Attachment.objects().where(
        Attachment.space_id.is_in(space_ids),
        Attachment.status == AttachmentStatus.READY.value,
    )

    spent = 0
    body_by_engram: dict[object, str | None] = {}
    engram_path_by_id = {engram.id: engram.path for engram in engrams}
    for engram in sorted(engrams, key=_priority):
        if spent + len(engram.body) <= char_budget:
            body_by_engram[engram.id] = engram.body
            spent += len(engram.body)
        else:
            body_by_engram[engram.id] = None

    included = sum(1 for body in body_by_engram.values() if body is not None)
    truncated = included < len(engrams)
    note = (
        f"{len(engrams) - included} engram(s) exceeded the context budget and are "
        "listed with body=null. Open them with open when relevant."
        if truncated
        else None
    )

    by_space = {
        space.id: SpaceContext(
            alias=aliases[space.id],
            version=space.version,
            engrams=[],
            attachments=[],
        )
        for space in spaces
    }
    for engram in sorted(engrams, key=lambda p: p.path):
        by_space[engram.space_id].engrams.append(
            {
                "path": engram.path,
                "title": engram.title,
                "tags": list(engram.tags),
                "updated": engram.updated_at.isoformat(),
                "size": len(engram.body),
                "version": engram.version,
                "body": body_by_engram[engram.id],
            }
        )
    for attachment in attachments:
        by_space[attachment.space_id].attachments.append(
            {
                "key": attachment.object_key,
                "filename": attachment.filename
                or attachment.object_key.rsplit("/", 1)[-1],
                "mime": attachment.mime,
                "size": attachment.byte_size,
                "description": attachment.description,
                "engram_path": engram_path_by_id.get(attachment.engram_id),
            }
        )

    version = (
        ";".join(f"{ctx.alias}={ctx.version}" for ctx in by_space.values()) or "empty"
    )
    return ContextPayload(
        version=version,
        truncated=truncated,
        note=note,
        engram_count=len(engrams),
        included_count=included,
        spaces=list(by_space.values()),
    )
