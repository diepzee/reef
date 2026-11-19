"""Find: the methods an assistant reads memory with.

- :func:`load_index` starts a conversation: the operating protocol, the
  persona, and the index of every engram and file, per space, with each
  space's members.
- :func:`recall` answers a question with passages, each saying where it
  comes from and giving a reference to open it.
- :func:`open` reads whole engrams and stored files by reference.
- :func:`catch_up` lists what changed in the caller's spaces.
- :func:`brief` prepares the assistant on a topic: the engrams that bear on
  it, and for a log only its last entries.

**References.** A reference names an engram or a file through the caller's
own name for its space: ``<space>/<path>`` for an engram,
``<space>/file/<key>`` for a file. Engram paths end in ``.md`` and file keys
never do, which is how the two are told apart. See :func:`_parse_ref`.

**Unreadable is not found.** :func:`open` answers a reference the caller may
not read exactly as it answers one to nothing at all. Row security hides the
first, the second does not exist, and both take the same path through the
code to the same answer.

Each method opens the short transactions it needs, armed for the caller.
:func:`recall` searches outside them, so no connection waits on the
embedding service or the reranker.
"""

from dataclasses import asdict
from datetime import datetime

from reef import usage
from reef.access import AccessDenied, Principal, accessible_spaces, alias_map
from reef.activity import whats_new
from reef.attachments import S3ObjectStore, get_attachment
from reef.config import get_settings
from reef.context import build_index, latest_editors
from reef.db import transaction_scope
from reef.engrams import get_engram, get_engram_as_of
from reef.links import links_of
from reef.methods import Caller, MethodError, logs, method
from reef.models import AccessKind, Attachment, Engram, MemberRole, Membership, Space
from reef.protocol import build_instructions
from reef.search import search_engrams
from reef.spaces import member_names, space_owner

#: What follows the space in a reference to a file.
FILE_PREFIX = "file/"

#: The window :func:`catch_up` reports when it is given no moment, in words.
#: The same window :func:`reef.activity.whats_new` uses.
DEFAULT_WINDOW = "the last 7 days"

#: The most characters of text :func:`brief` returns in all.
BRIEF_BUDGET = 30_000

#: The most characters of one engram body :func:`brief` returns.
BRIEF_BODY_CHARS = 8_000

#: How many engrams :func:`brief` asks the ranking for.
BRIEF_ENGRAMS = 5

#: The fewest and most entries :func:`brief` returns from one log.
BRIEF_ENTRIES = (1, 100)

NOT_FOUND = "not_found"
"""The answer for a reference to nothing, or to something the caller may not read."""


@method("load_index")
async def load_index(caller: Caller, reason: str) -> dict:
    """Return the operating protocol, the persona and the index, per space.

    The index lists every engram the caller can see, without its body, and
    every described file. Each space also lists its members by display
    name, so the assistant knows who will read what it writes there, and
    says what the caller may do there and who owns it, so the assistant
    knows before writing whether it can.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :returns: ``version``, ``spaces`` and ``operating_protocol``, the
        protocol and the persona. Each space has ``alias``, ``version``,
        ``engrams``, ``attachments``, ``members``; ``role``, the caller's
        own (see :func:`_role`); and ``owner``, the owner's display name
    """
    principal = caller.principal
    async with transaction_scope():
        index = asdict(await build_index(principal))
        aliases = await alias_map(principal)
        spaces = {space.id: space for space in await accessible_spaces(principal)}
        roles = {
            row["space_id"]: row["role"]
            for row in await Membership.select(
                Membership.space_id, Membership.role
            ).where(Membership.person_id == principal.person_id)
        }
        space_ids = {alias: space_id for space_id, alias in aliases.items()}
        for space in index["spaces"]:
            space_id = space_ids[space["alias"]]
            owner = await space_owner(space_id)
            space["members"] = await member_names(space_id)
            space["role"] = _role(principal, spaces[space_id], roles.get(space_id))
            space["owner"] = owner["display_name"] if owner else None
        index["operating_protocol"] = await build_instructions(principal)
    return index


def _role(principal: Principal, space: Space, membership: str | None) -> str:
    """Say what the caller may do in a space, in one word.

    :param principal: the caller
    :param space: the space
    :param membership: the role of the caller's membership
    :returns: ``owner``, who may write and manages the space on the website;
        ``member``, who may write; or ``viewer``, who may only read. With no
        membership on record, ``viewer``: the answer that promises least
    """
    if space.owner_person_id == principal.person_id:
        return "owner"
    return membership or MemberRole.VIEWER.value


@method("recall")
async def recall(
    caller: Caller,
    reason: str,
    question: str,
    space: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Return the passages that best answer a question, best first.

    Ranked by the same search the search tools use, by words and by meaning,
    and read again by the reranker for a large memory. See
    :func:`reef.search.search_engrams`. A described file is found by its
    name and description.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param question: the question, in the person's words
    :param space: search one space, by the caller's name for it; all when None
    :param limit: how many passages, at most
    :returns: one dict per hit: ``kind`` (``engram`` or ``file``), ``space``,
        ``engram`` (its path; None for a file), ``title``, ``passage``,
        ``author`` (who changed it last; None for a file), ``updated_at`` and
        ``ref``, the reference :func:`open` takes
    :raises MethodError: ``unknown_space`` when ``space`` names no space of
        the caller's
    """
    principal = caller.principal
    try:
        hits = await search_engrams(
            principal, question, space, limit, log=True, passages=True
        )
    except AccessDenied:
        raise MethodError("unknown_space") from None
    if not hits:
        return []
    async with transaction_scope():
        sources = await _sources(principal, hits)
    passages = []
    for hit in hits:
        locator = hit["key"] if hit["kind"] == "file" else hit["path"]
        source = sources.get((hit["kind"], hit["cove"], locator))
        # Gone since the search found it: nothing to point to.
        if source is not None:
            passages.append(_passage(hit, *source))
    return passages


async def _sources(
    principal: Principal, hits: list[dict]
) -> dict[tuple[str, str, str], tuple[str | None, str]]:
    """Find who changed each hit last, and when.

    :param principal: the caller
    :param hits: what the search found
    :returns: by search kind, space and path or key: the author's display
        name, and the time as ISO 8601. A file records no author.
    """
    aliases = await alias_map(principal)
    space_ids = {alias: space_id for space_id, alias in aliases.items()}
    engrams = [hit for hit in hits if hit["kind"] == "page"]
    files = [hit for hit in hits if hit["kind"] == "file"]
    sources: dict[tuple[str, str, str], tuple[str | None, str]] = {}
    if engrams:
        rows = await Engram.select(
            Engram.id, Engram.space_id, Engram.path, Engram.updated_at
        ).where(
            Engram.space_id.is_in([space_ids[hit["cove"]] for hit in engrams]),
            Engram.path.is_in([hit["path"] for hit in engrams]),
        )
        editors = await latest_editors([row["id"] for row in rows])
        for row in rows:
            sources[("page", aliases[row["space_id"]], row["path"])] = (
                editors[row["id"]],
                row["updated_at"].isoformat(),
            )
    if files:
        rows = await Attachment.select(
            Attachment.space_id, Attachment.object_key, Attachment.created_at
        ).where(
            Attachment.space_id.is_in([space_ids[hit["cove"]] for hit in files]),
            Attachment.object_key.is_in([hit["key"] for hit in files]),
        )
        for row in rows:
            sources[("file", aliases[row["space_id"]], row["object_key"])] = (
                None,
                row["created_at"].isoformat(),
            )
    return sources


def _passage(hit: dict, author: str | None, updated_at: str) -> dict:
    """Shape one search hit as a passage :func:`recall` returns.

    :param hit: the hit, with its ``passage`` when it is an engram
    :param author: who changed it last
    :param updated_at: when, as ISO 8601
    :returns: the passage
    """
    space = hit["cove"]
    if hit["kind"] == "file":
        return {
            "kind": "file",
            "space": space,
            "engram": None,
            "title": hit["filename"] or hit["key"].rsplit("/", 1)[-1],
            "passage": hit["snippet"],
            "author": author,
            "updated_at": updated_at,
            "ref": f"{space}/{FILE_PREFIX}{hit['key']}",
        }
    return {
        "kind": "engram",
        "space": space,
        "engram": hit["path"],
        "title": hit["title"],
        "passage": hit["passage"],
        "author": author,
        "updated_at": updated_at,
        "ref": f"{space}/{hit['path']}",
    }


@method("open")
async def open(
    caller: Caller, reason: str, refs: list[str], as_of: str | None = None
) -> list[dict]:
    """Read whole engrams and stored files by reference, in the order asked.

    An engram comes whole, with its links. With ``as_of`` it comes as it
    stood at that moment, from its history, without a version or links:
    the past cannot be edited. A file comes as its description and a
    short-lived URL to its bytes; ``as_of`` does not apply to files.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param refs: references as :func:`recall` gives them:
        ``<space>/<path>`` or ``<space>/file/<key>``
    :param as_of: a moment in ISO 8601, to read engrams as they stood then
    :returns: one dict per reference, carrying the ``ref``. A reference to
        nothing, or to something the caller may not read, gives
        ``{"ref", "error": "not_found"}``.
    :raises MethodError: ``invalid_as_of`` when ``as_of`` is not a moment
    """
    moment = _moment(as_of, "invalid_as_of")
    async with transaction_scope():
        return [await _open_one(caller.principal, ref, moment, as_of) for ref in refs]


async def _open_one(
    principal: Principal, ref: str, moment: datetime | None, as_of: str | None
) -> dict:
    """Read what one reference names, or say it is not found.

    :param principal: the caller
    :param ref: the reference
    :param moment: the moment to read engrams as of, or None for now
    :param as_of: that moment as the caller wrote it
    :returns: what was read, or the not-found answer
    """
    found = None
    parsed = _parse_ref(ref)
    if parsed is not None:
        space, kind, locator = parsed
        try:
            if kind == "file":
                found = await _read_file(principal, space, locator)
            elif moment is not None:
                found = await _read_past(principal, space, locator, moment, as_of)
            else:
                found = await _read_engram(principal, space, locator)
        # A space the caller has no name for: as good as an engram not there.
        except AccessDenied:
            found = None
    if found is None:
        return {"ref": ref, "error": NOT_FOUND}
    return {"ref": ref, **found}


def _parse_ref(ref: str) -> tuple[str, str, str] | None:
    """Take a reference apart at its first ``/``.

    What follows the space is a file key when it starts with ``file/`` and
    does not end in ``.md``; otherwise it is an engram path. A reference with
    an empty part, or a NUL character, which Postgres text cannot hold, is
    malformed.

    :param ref: the reference
    :returns: space, kind (``engram`` or ``file``) and path or key; None
        for a malformed reference
    """
    space, _, rest = ref.partition("/")
    if not space or not rest or "\x00" in ref:
        return None
    if rest.startswith(FILE_PREFIX) and not rest.endswith(".md"):
        key = rest.removeprefix(FILE_PREFIX)
        return (space, "file", key) if key else None
    return space, "engram", rest


async def _read_engram(principal: Principal, space: str, path: str) -> dict | None:
    """Read one engram as it is now, and record the read.

    :param principal: the caller
    :param space: the caller's name for its space
    :param path: its path
    :returns: the engram, or None
    :raises AccessDenied: when the caller has no space of that name
    """
    engram = await get_engram(principal, space, path)
    if engram is None:
        return None
    await usage.record(principal, engram.id, AccessKind.READ)
    return {
        "kind": "engram",
        "space": space,
        "engram": engram.path,
        "title": engram.title,
        "tags": list(engram.tags),
        "body": engram.body,
        "version": engram.version,
        "updated_at": engram.updated_at.isoformat(),
        **await links_of(principal, engram.id),
    }


async def _read_past(
    principal: Principal, space: str, path: str, moment: datetime, as_of: str | None
) -> dict | None:
    """Read one engram as it stood at a moment.

    :param principal: the caller
    :param space: the caller's name for its space
    :param path: its path
    :param moment: the moment
    :param as_of: the moment as the caller wrote it
    :returns: the engram then, or None if it did not exist then
    :raises AccessDenied: when the caller has no space of that name
    """
    state = await get_engram_as_of(principal, space, path, moment)
    if state is None:
        return None
    return {
        "kind": "engram",
        "space": space,
        "engram": state["path"],
        "title": state["title"],
        "tags": state["tags"],
        "body": state["body"],
        "updated_at": state["created"],
        "as_of": as_of,
    }


async def _read_file(principal: Principal, space: str, key: str) -> dict | None:
    """Read one stored file's description and a short-lived URL to its bytes.

    :param principal: the caller
    :param space: the caller's name for its space
    :param key: the file's key
    :returns: the file, or None
    :raises AccessDenied: when the caller has no space of that name
    """
    attachment = await get_attachment(principal, space, key)
    if attachment is None:
        return None
    ttl = get_settings().files.signed_url_ttl_seconds
    filename = attachment.filename or key.rsplit("/", 1)[-1]
    url = await S3ObjectStore().signed_url(
        key, ttl, mime=attachment.mime, filename=filename
    )
    return {
        "kind": "file",
        "space": space,
        "key": key,
        "filename": filename,
        "mime": attachment.mime,
        "size": attachment.byte_size,
        "description": attachment.description,
        "url": url,
        "expires_in": ttl,
    }


@method("catch_up")
async def catch_up(caller: Caller, reason: str, since: str | None = None) -> dict:
    """List what changed in the caller's spaces: who wrote what, where, when.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param since: a moment in ISO 8601; the last 7 days when None
    :returns: ``since``, the window in words, and ``events``, newest first.
        See :func:`reef.activity.whats_new`.
    :raises MethodError: ``invalid_since`` when ``since`` is not a moment
    """
    moment = _moment(since, "invalid_since")
    async with transaction_scope():
        events = await whats_new(caller.principal, since=moment)
    return {"since": since if since is not None else DEFAULT_WINDOW, "events": events}


def _moment(text: str | None, code: str) -> datetime | None:
    """Read a moment the caller sent.

    :param text: the moment in ISO 8601, or None
    :param code: the error code to refuse an unreadable moment with
    :returns: the moment, or None when none was sent
    :raises MethodError: with ``code`` when the text is not a moment
    """
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise MethodError(code) from None


@method("brief")
async def brief(
    caller: Caller,
    reason: str,
    topic: str,
    space: str | None = None,
    entries: int = 10,
) -> dict:
    """Return the engrams that bear on a topic, ready to advise or prepare on.

    Ranked by :func:`reef.search.search_engrams`, engrams only. A log, an
    engram tagged ``log``, gives its last ``entries`` entries across all its
    parts (see :mod:`reef.methods.logs`); several hits in one log make one
    item, at the best hit's place. Any other engram gives its body, cut to
    :data:`BRIEF_BODY_CHARS`. In all the text is cut to
    :data:`BRIEF_BUDGET` characters: items fill in rank order, the one that
    would pass the budget is cut to what remains, and later ones are left
    out. A log drops its oldest entries first, a body is cut at its end.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param topic: what to prepare on, in the person's words
    :param space: search one space, by the caller's name for it; all when None
    :param entries: how many entries to take from a log, from 1 to 100
    :returns: ``engrams``, best first, each with ``space``, ``engram`` (the
        path; a log's first part), ``title``, ``entries`` for a log or
        ``body`` for anything else, and ``parts``, the paths read, first to
        last
    :raises MethodError: ``unknown_space`` when ``space`` names no space of
        the caller's
    """
    principal = caller.principal
    wanted = max(BRIEF_ENTRIES[0], min(entries, BRIEF_ENTRIES[1]))
    try:
        hits = await search_engrams(principal, topic, space, BRIEF_ENGRAMS, log=False)
    except AccessDenied:
        raise MethodError("unknown_space") from None
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    remaining = BRIEF_BUDGET
    async with transaction_scope():
        for hit in hits:
            if hit["kind"] != "page" or remaining <= 0:
                continue
            item = await _brief_item(caller, hit, wanted, remaining)
            if item is None:
                continue
            key = (item["space"], item["engram"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            remaining -= len(
                "".join(item["entries"]) if "entries" in item else item["body"]
            )
    return {"engrams": items}


async def _brief_item(
    caller: Caller, hit: dict, wanted: int, remaining: int
) -> dict | None:
    """Read what one hit contributes to a brief.

    :param caller: who is calling
    :param hit: an engram hit from the search
    :param wanted: how many entries to take from a log
    :param remaining: how many characters the brief may still add
    :returns: the item, or None when the engram is gone since the search
    """
    principal = caller.principal
    space, path = hit["cove"], hit["path"]
    parts = await logs.series(caller, space, path)
    if parts:
        taken = logs.last_entries(parts, wanted)
        item = {
            "space": space,
            "engram": parts[0].path,
            "title": parts[0].title,
            "entries": _fit_entries(taken, remaining),
            "parts": [part.path for part in parts],
        }
    else:
        engram = await get_engram(principal, space, path)
        if engram is None:
            return None
        parts = [engram]
        item = {
            "space": space,
            "engram": engram.path,
            "title": engram.title,
            "body": engram.body[: min(BRIEF_BODY_CHARS, remaining)],
            "parts": [engram.path],
        }
    for part in parts:
        await usage.record(principal, part.id, AccessKind.READ)
    return item


def _fit_entries(entries: list[str], room: int) -> list[str]:
    """Keep the newest entries that fit in a number of characters.

    :param entries: the entries, oldest first
    :param room: how many characters may be used
    :returns: the newest entries that fit, oldest first; when not even the
        newest fits, that one cut at its end
    """
    kept: list[str] = []
    used = 0
    for entry in reversed(entries):
        if used + len(entry) > room:
            break
        kept.append(entry)
        used += len(entry)
    if not kept and entries:
        kept = [entries[-1][:room]]
    return kept[::-1]
