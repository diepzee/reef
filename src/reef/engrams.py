"""Engram reads and versioned writes.

Every function here assumes it runs inside :func:`reef.db.transaction_scope`
and arms RLS through :func:`reef.access.resolve_space` before touching
content. There is no session parameter to thread: Piccolo binds queries to
the ambient transaction, and an unarmed one returns nothing.
"""

import re
from datetime import datetime

from piccolo.columns.defaults.timestamp import TimestampNow

from reef import audit, embedding, usage
from reef.access import AccessDenied, Principal, resolve_space, resolve_writable_space
from reef.config import get_settings
from reef.indexfields import index_fields
from reef.leakguard import overlaps
from reef.links import (
    RelationRefused,
    Resolver,
    connect_waiting_links,
    lock_typed_links,
    pending_replacements,
    sync_typed_links,
    sync_wiki_links,
)
from reef.models import AccessKind, Attachment, Engram, Revision, Space, SpaceKind
from reef.relations import Relations, refuse_stored_block

PROTECTED_PREFIX = "meta/"

PERSONA_PATH = "meta/persona.md"
"""The persona engram, and the one personal engram exempt from the leak guard.

Defined here rather than in :mod:`reef.protocol`, which re-exports it: that
module imports this one, so the reverse would be a cycle.
"""

#: Characters a normalized engram path may contain.
_ALLOWED = re.compile(r"[a-z0-9\-/._]")


def _now() -> datetime:
    """Return the instant a write happened, as ``created_at`` records it.

    Produced by the same default the timestamp columns use, so that
    ``updated_at`` and ``created_at`` always compare on the same clock.

    :returns: a naive ``datetime``
    """
    return TimestampNow().python()


class VersionConflict(Exception):
    """Raised when expected_version does not match the current engram version."""


class ProtectedPath(Exception):
    """Raised when a generic write targets the protected meta/ namespace."""


class InvalidPath(Exception):
    """Raised when an engram path cannot be repaired into a usable one."""


class PrivateContentLeak(Exception):
    """Raised when a space write carries text copied from the personal space."""


async def _refuse_private_copy(
    principal: Principal,
    alias: str,
    path: str,
    body: str,
    existing: Engram | None,
) -> None:
    """Refuse a shared write that copies the caller's own personal engrams.

    This is what makes the share ceremony a boundary rather than a
    convention; :mod:`reef.leakguard` explains the threat it answers and,
    just as importantly, what it does not catch.

    The personal engrams are read as the caller, under the same policies as
    any other read, so this discloses nothing the caller could not already
    fetch. The persona is skipped: it is seeded from a fixed template, so
    every person's copy shares its wording with every other's, and matching
    against it would refuse writes over text that is not private at all.

    :param principal: the authenticated person
    :param alias: the destination space
    :param path: the engram being written, for the message
    :param body: the body about to be written
    :param existing: the stored engram, whose text is exempt
    :raises PrivateContentLeak: when the write introduces private text
    """
    try:
        personal = await resolve_space(principal, "personal")
    except AccessDenied:
        # No personal space, so nothing private to copy out of. Onboarding
        # creates one for everybody, but a principal without one must still
        # be able to write to a space -- a guard that refuses the write it
        # cannot evaluate would turn a missing row into a lockout.
        return
    private = (
        await Engram.select(Engram.body)
        .where(Engram.space_id == personal.id, Engram.path != PERSONA_PATH)
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
        "share: its first call shows the user the exact text and who will be "
        "able to read it, and its second call performs the move after they "
        "agree. If the wording only coincides, rewrite it in your own words "
        "for this space."
    )


class EngramTooLarge(Exception):
    """Raised when a body exceeds the per-engram character ceiling."""


def validate_body(path: str, body: str) -> None:
    """Reject an engram body past the per-engram ceiling.

    Engrams had no size limit at all while files were capped at 25 MB, so the
    cheapest way to bloat a space was to write prose into it. The cost is not
    storage: every body is parsed for search and for the index on each save,
    and loaded whole by anything that reads the engram, so one oversized engram
    is paid for by every member who opens it.

    :param path: the engram path, for the message
    :param body: the full markdown body
    :raises EngramTooLarge: if the body exceeds ``REEF_ENGRAM_MAX_CHARS``
    """
    ceiling = get_settings().engrams.max_chars
    if len(body) > ceiling:
        raise EngramTooLarge(
            f"{path!r} is {len(body)} characters, over the {ceiling}-character "
            "engram limit; split it into several engrams"
        )


def normalize_path(path: str) -> str:
    """Return the path a new engram will actually be stored under.

    Mirrors ``frontend/src/pagePath.ts`` so the browser, the CLI, and an
    assistant calling ``write`` all name an engram the same way. Before
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
        raise InvalidPath("an engram needs a path")
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
        raise InvalidPath(f"{path!r} names no engram, only an extension")
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


async def list_engrams(principal: Principal, alias: str) -> list[Engram]:
    """List every engram in one of the principal's spaces.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :returns: engrams ordered by path
    """
    space = await resolve_space(principal, alias)
    return (
        await Engram.objects().where(Engram.space_id == space.id).order_by(Engram.path)
    )


async def get_engram(principal: Principal, alias: str, path: str) -> Engram | None:
    """Fetch a single engram by path.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: engram path, for example ``house.md``
    :returns: the engram, or None if absent from that space
    """
    space = await resolve_space(principal, alias)
    return (
        await Engram.objects()
        .where(Engram.space_id == space.id, Engram.path == path)
        .first()
    )


async def get_engram_as_of(
    principal: Principal, alias: str, path: str, as_of: datetime
) -> dict | None:
    """Fetch the state an engram had at a moment in the past.

    Each revision snapshots the state its write produced, so the newest
    revision at or before ``as_of`` *is* the engram as it stood then. The
    lookup runs under the same armed RLS as a present-day read: history is
    exactly as private as the engram it belongs to.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: engram path, for example ``house.md``
    :param as_of: the moment to reconstruct; aware datetimes are converted
        to the naive local time the revision timestamps are stored in
    :returns: the engram fields at that moment, or None if it did not exist
    """
    if as_of.tzinfo is not None:
        as_of = as_of.astimezone().replace(tzinfo=None)
    engram = await get_engram(principal, alias, path)
    if engram is None:
        return None
    revision = (
        await Revision.objects()
        .where(Revision.engram_id == engram.id, Revision.created_at <= as_of)
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


async def validate_save(
    principal: Principal,
    alias: str,
    space: Space,
    path: str,
    body: str,
    existing: Engram | None,
    *,
    expected_version: int | None = None,
    allow_protected: bool = False,
    allow_private_copy: bool = False,
) -> None:
    """Check a resolved, locked save before writing or asking for confirmation.

    :param principal: the writer
    :param alias: the writer's name for the destination space
    :param space: the resolved writable space
    :param path: the existing or normalized path
    :param body: the body that would be stored
    :param existing: the current engram, locked by the caller, or None
    :param expected_version: the requested version, or None to allow any
    :param allow_protected: allow a dedicated tool to change meta/
    :param allow_private_copy: the share ceremony already disclosed the text
    :raises EngramTooLarge: when the body exceeds the limit
    :raises ProtectedPath: when the caller cannot write under meta/
    :raises PrivateContentLeak: when the write copies private text
    :raises VersionConflict: when the current version differs
    :raises reef.relations.InvalidRelations: ``relation_block_in_body`` when
        the body could be read as a leading relation block
    """
    validate_body(path, body)
    refuse_stored_block(body)
    _refuse_protected(path, allow_protected)
    if not allow_private_copy and space.kind == SpaceKind.SHARED.value:
        await _refuse_private_copy(principal, alias, path, body, existing)
    if existing is None:
        if expected_version not in (None, 0):
            raise VersionConflict(f"{path!r} does not exist yet")
    elif expected_version is not None and existing.version != expected_version:
        raise VersionConflict(
            f"{path!r} is at version {existing.version}, expected {expected_version}"
        )


async def save_engram(
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
    via: AccessKind | None = AccessKind.WRITE,
    channel: str = "assistant",
    relations: Relations | None = None,
    replacements_confirmed: bool = False,
) -> Engram:
    """Create or overwrite an engram: snapshot a revision, bump engram and space versions.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: engram path
    :param body: full markdown body
    :param message: why this write happened, stored on the revision
    :param title: human-readable title; defaults to the path stem on creation
    :param tags: engram tags; unchanged on update when omitted
    :param expected_version: optimistic lock; mismatch raises VersionConflict
    :param allow_protected: permit writes under ``meta/`` (dedicated tools only)
    :param allow_private_copy: permit shared text copied from the personal
        space. Only the share ceremony passes this -- it *is* the sanctioned
        copy, having already shown the user the exact disclosure.
    :param via: how to record this write in the principal's usage log, or
        None to record nothing -- for a write that is not the person using
        the engram
    :param channel: who made the change, kept on the revision: ``assistant``
        for a call over MCP, ``person`` for the website
    :param relations: relations already read by write or the web save route
    :param replacements_confirmed: the caller checked the person's yes
    :raises PrivateContentLeak: for personal text written into a space
    :raises ProtectedPath: for meta/ paths without allow_protected
    :raises InvalidPath: when a new engram's path cannot be normalized
    :raises EngramTooLarge: for a body over the per-engram ceiling
    :raises VersionConflict: when expected_version is stale
    :raises reef.relations.InvalidRelations: ``relation_block_in_body`` for
        a stored relation block, ``relation_invalid`` for an unknown kind,
        or ``relation_too_many`` for more than twenty targets of a kind
    :raises RelationRefused: ``relation_target_missing``,
        ``relation_into_personal``, ``relation_to_itself``,
        ``relation_needs_write``, ``relation_circle`` or
        ``relation_needs_confirmation`` when a typed link cannot be saved
    :returns: the saved engram
    """
    validate_body(path, body)
    refuse_stored_block(body)
    relations = relations or Relations({})
    _refuse_protected(path, allow_protected)
    space = await resolve_writable_space(principal, alias)
    if not relations.empty:
        await lock_typed_links()
    # An existing engram is addressed by exactly the name it already carries.
    # reef stored arbitrary paths before normalize_path existed, and
    # normalizing unconditionally would not *rename* such an engram -- it would
    # write a second one beside it under the tidy name and strand the
    # original. So: old engrams keep their names, new engrams get good ones, and
    # the mess stops growing without anything already stored breaking.
    engram = (
        await Engram.objects()
        .where(Engram.space_id == space.id, Engram.path == path)
        .lock_rows()
        .first()
    )
    if engram is None:
        path = normalize_path(path)
        _refuse_protected(path, allow_protected)
        engram = (
            await Engram.objects()
            .where(Engram.space_id == space.id, Engram.path == path)
            .lock_rows()
            .first()
        )
    await validate_save(
        principal,
        alias,
        space,
        path,
        body,
        engram,
        expected_version=expected_version,
        allow_protected=allow_protected,
        allow_private_copy=allow_private_copy,
    )
    if not relations.empty and channel != "person" and not replacements_confirmed:
        resolver = await Resolver.for_person(principal)
        if await pending_replacements(resolver, space.id, path, relations):
            raise RelationRefused("relation_needs_confirmation", path)
    created = False
    if engram is None:
        now = _now()
        engram = Engram(
            space_id=space.id,
            path=path,
            title=title or path.removesuffix(".md"),
            tags=tags or [],
            body=body,
            version=1,
            indexed_version=1,
            linked_version=1,
            created_at=now,
            updated_at=now,
            **index_fields(body),
        )
        await engram.save()
        created = True
    else:
        engram.body = body
        engram.updated_at = _now()
        engram.version += 1
        engram.indexed_version = engram.version
        engram.linked_version = engram.version
        for field, value in index_fields(body).items():
            setattr(engram, field, value)
        if title is not None:
            engram.title = title
        if tags is not None:
            engram.tags = tags
        await engram.save()

    await Revision(
        engram_id=engram.id,
        path=engram.path,
        title=engram.title,
        tags=list(engram.tags),
        body=body,
        message=message,
        author_id=principal.person_id,
        channel=channel,
        created_at=engram.updated_at,
    ).save()
    await Space.update({Space.version: Space.version + 1}).where(Space.id == space.id)
    # After the engram row is written: the links point at it, and retiring old
    # ones reads the targets it now stores.
    resolver = await Resolver.for_person(principal)
    await sync_wiki_links(resolver, engram.id, space.id, engram.reference_targets)
    if not relations.empty:
        await sync_typed_links(resolver, engram.id, space.id, relations)
    if created:
        await connect_waiting_links(resolver, engram.id, space.id, engram.path)
    if via is not None:
        await usage.record(principal, engram.id, via)
    await embedding.request(principal, engram.id)
    return engram


class EngramNotFound(Exception):
    """Raised when an engram to be deleted is not in that space."""


async def delete_engram(principal: Principal, alias: str, path: str) -> dict:
    """Delete an engram and its history, permanently.

    The counterpart to a mistyped path. Until this existed an engram could be
    blanked but never removed, so a fat-fingered name was a permanent
    resident of the space and the only way out was deleting the whole space.

    Any member may delete, which is the same authority they already have to
    overwrite the engram with nothing -- the difference is that this also takes
    the revisions, so it is a genuine loss and the caller is expected to have
    confirmed it. ``meta/`` is refused: the persona and protocol engrams are
    part of the machinery, and a space without them is not a state the rest of
    the code is written to expect.

    Attachments that hung off the engram survive it -- ``engram_id`` is
    ``ON DELETE SET NULL`` -- because the bytes were uploaded deliberately
    and are reachable in the space's file list on their own.

    Recorded in the audit trail for the reason ``audit`` gives for space
    deletion: once the engram and its revisions are gone, nothing left in the
    database can say it existed or who ended it.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param path: the exact path of the engram to delete
    :raises ProtectedPath: for an engram under ``meta/``
    :raises EngramNotFound: if that space has no engram at that path
    :returns: the deleted path and how many revisions went with it
    """
    _refuse_protected(path, allow_protected=False)
    space = await resolve_writable_space(principal, alias)
    engram = (
        await Engram.objects()
        .where(Engram.space_id == space.id, Engram.path == path)
        .lock_rows()
        .first()
    )
    if engram is None:
        raise EngramNotFound(f"no engram at {path!r}")

    revisions = await Revision.count().where(Revision.engram_id == engram.id)
    # Explicitly, and before the engram: the revision -> engram foreign key has no
    # cascade, so leaving this to the database would refuse the delete rather
    # than tidy up after it.
    await Revision.delete().where(Revision.engram_id == engram.id)
    await Attachment.update({Attachment.engram_id: None}).where(
        Attachment.engram_id == engram.id
    )
    await Engram.delete().where(Engram.id == engram.id)
    await Space.update({Space.version: Space.version + 1}).where(Space.id == space.id)
    audit.record(
        audit.ENGRAM_DELETED,
        actor=principal.person_id,
        space_id=space.id,
        revisions=revisions,
    )
    return {"deleted": True, "path": path, "revisions": revisions}
