"""Capture: the methods an assistant keeps new memory with.

- :func:`remember` adds one quick fact to a space's inbox.
- :func:`log` adds one entry at the end of a log, and starts the log's next
  part when the entry would not fit. See :mod:`reef.methods.logs`.
- :func:`write` creates or replaces whole engrams, several at once.
- :func:`store_file` keeps a document or an image with its description.

**Who wrote it.** Every change keeps the caller's writer kind on its
revision, so history can tell an assistant's change from a person's.

**Refusals.** What the storage layer refuses becomes a :class:`MethodError`
with a plain message, by :func:`reef.methods.refusals.refusals`.

Each method opens the transactions it needs, armed for the caller.
:func:`store_file` opens none itself: :func:`reef.attachments.add_attachment`
opens its own two.
"""

import base64
import binascii
import re
from datetime import UTC, datetime

from asyncpg import UniqueViolationError

from reef.access import (
    Principal,
    resolve_writable_space,
)
from reef.attachments import MIME_RE, S3ObjectStore, add_attachment
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import (
    PERSONA_PATH,
    InvalidPath,
    get_engram,
    normalize_path,
    save_engram,
)
from reef.limits import FILENAME_CHARS, MAX_BATCH, MIME_CHARS
from reef.methods import MESSAGES, Caller, MethodError, logs, method
from reef.methods.find import FILE_PREFIX
from reef.methods.refusals import refusals
from reef.models import AccessKind, Engram
from reef.sections import HEADING_CHARS

#: The engram :func:`remember` adds facts to, in each space.
INBOX = "inbox.md"

#: One recorded inbox line. The fact is whatever follows the date stamp.
_ENTRY_RE = re.compile(r"^- \(\d{4}-\d{2}-\d{2}\) (?P<fact>.*)$")


async def _save(
    caller: Caller,
    space: str,
    path: str,
    body: str,
    *,
    message: str,
    title: str | None = None,
    tags: list[str] | None = None,
    expected_version: int | None = None,
    via: AccessKind | None = AccessKind.WRITE,
) -> Engram:
    """Save an engram as the caller, recording who wrote it.

    :param caller: who is writing
    :param space: the caller's name for the space
    :param path: the engram's path
    :param body: its whole new body
    :param message: why, kept on the revision
    :param title: its title; unchanged, or the path's stem for a new one,
        when None
    :param tags: its tags; unchanged, or none for a new one, when None
    :param expected_version: the version it must be at, 0 for one that
        must not exist yet; any when None
    :param via: how to record the write in the usage log, or None
    :returns: the saved engram
    :raises MethodError: when the write is refused
    """
    with refusals(path):
        return await save_engram(
            caller.principal,
            space,
            path,
            body,
            message=message,
            title=title,
            tags=tags,
            expected_version=expected_version,
            via=via,
            channel=caller.writer,
        )


# --- remember --------------------------------------------------------------------


def _already_recorded(body: str, fact: str) -> bool:
    """Report whether this exact fact is already an entry in the inbox.

    Entry-by-entry, not ``fact in body``. A substring test calls a genuinely
    new fact a duplicate whenever some longer entry happens to contain its
    words -- "allergic to penicillin" is silently discarded once "allergic to
    penicillin and nuts" has been written -- and in a memory product a write
    that reports success and stores nothing is the worst failure available.
    It also let anyone sharing a space suppress future writes to its inbox
    by padding the engram with likely phrasings.

    :param body: the inbox engram's markdown body
    :param fact: the fact about to be appended
    :returns: True if an entry already records exactly this fact
    """
    wanted = fact.strip()
    for line in body.splitlines():
        match = _ENTRY_RE.match(line.strip())
        if match and match.group("fact").strip() == wanted:
            return True
    return False


async def add_to_inbox(
    principal: Principal, space: str, fact: str, *, channel: str
) -> str | None:
    """Append one fact to a space's inbox, locking the row and deduplicating.

    The row lock serializes concurrent appends; the exact-entry check makes
    transport retries harmless without swallowing facts that merely resemble
    an existing one. Runs in the transaction already open.

    :param principal: the authenticated person
    :param space: ``personal`` or one of the person's names for a space
    :param fact: the text to record
    :param channel: who made the change, kept on the revision
    :returns: the entry written, or None when the inbox already has it
    :raises reef.access.AccessDenied: when the person has no such space
    :raises reef.access.ReadOnlyMembership: when they may only read it
    :raises reef.engrams.PrivateContentLeak: for personal text in a space
    """
    resolved = await resolve_writable_space(principal, space)
    inbox = (
        await Engram.objects()
        .where(Engram.space_id == resolved.id, Engram.path == INBOX)
        .lock_rows()
        .first()
    )
    if inbox is not None and _already_recorded(inbox.body, fact):
        return None
    stamp = datetime.now(UTC).date().isoformat()
    entry = f"- ({stamp}) {fact}"
    body = f"{inbox.body}\n{entry}" if inbox else f"# Inbox\n\n{entry}"
    await save_engram(
        principal,
        space,
        INBOX,
        body,
        message=f"remember: {fact[:60]}",
        title="Inbox",
        via=AccessKind.REMEMBER,
        channel=channel,
    )
    return entry


@method("remember")
async def remember(
    caller: Caller, reason: str, fact: str, space: str = "personal"
) -> dict:
    """Add a quick fact to a space's inbox, the personal one unless named.

    The personal default is deliberate: sharing cannot be undone in effect,
    so the default place must be the private one. Sending the same fact
    again adds nothing.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param fact: the fact, in a sentence
    :param space: ``personal`` or one of the caller's names for a space
    :returns: ``space``, ``engram`` (the inbox's path) and ``duplicate``;
        with ``appended``, the line written, when it was not a duplicate
    :raises MethodError: ``unknown_space``, ``read_only`` or
        ``private_content``
    """
    with refusals(INBOX):
        async with transaction_scope():
            entry = await add_to_inbox(
                caller.principal, space, fact, channel=caller.writer
            )
    if entry is None:
        return {"space": space, "engram": INBOX, "duplicate": True}
    return {"space": space, "engram": INBOX, "appended": entry, "duplicate": False}


# --- log -------------------------------------------------------------------------


class _Raced(Exception):
    """Another call created the same log first, between our read and our write."""


def _opening(previous: str) -> str:
    """Write the line a new part opens with.

    :param previous: the path of the part before it
    :returns: a wiki link back to that part
    """
    return f"Continued from [[{previous}]]."


def _closing(following: str) -> str:
    """Write the line a full part closes with.

    :param following: the path of the part after it
    :returns: a wiki link on to that part, on a paragraph of its own
    """
    return f"\n\nContinued in [[{following}]].\n"


def _leaves_room(body: str, base: str, following: int, cap: int) -> bool:
    """Say whether a part's body fits, with room left for its closing line.

    The room is kept so that closing the part, when the log goes on in the
    next one, cannot take it past the cap.

    :param body: the part's body
    :param base: the path of the log's first part
    :param following: the number the part after it would have
    :param cap: the most characters an engram may hold
    :returns: True when the body and its closing line fit
    """
    return len(body) + len(_closing(logs.part_path(base, following))) <= cap


def _too_long(cap: int) -> MethodError:
    """Build the refusal of an entry that does not fit even on its own.

    :param cap: the most characters an engram may hold
    :returns: the error
    """
    return MethodError(
        "entry_too_long",
        f"{MESSAGES['entry_too_long']} One engram holds at most {cap:,} characters.",
    )


def _written(engram: Engram, part: int) -> dict:
    """Shape what :func:`log` returns.

    :param engram: the part written
    :param part: its number, 1 for the first
    :returns: ``engram`` (its path), ``part`` and ``version``
    """
    return {"engram": engram.path, "part": part, "version": engram.version}


@method("log")
async def log(
    caller: Caller, reason: str, space: str, engram: str, heading: str, entry: str
) -> dict:
    """Add one entry at the end of a log, and change nothing else.

    The entry is its heading, a blank line and the text, after a blank
    line, at the end of the log's last part. The heading takes the level the
    log's entries already use (see :func:`reef.methods.logs.entry_level`):
    ``##`` in a log of ``##`` entries, else ``###``. A log that does not
    exist yet is created, tagged ``log``. When the entry would take the last
    part past the engram size cap, the log goes on in a new part,
    ``<stem>-<n>.md``: tagged ``log``, titled like the first part with
    ``(part n)``, opening with a link back. The part before gets a closing
    link to it. No text moves.

    Calls on one log take turns, so entries sent at the same time all land.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param space: the caller's name for the space
    :param engram: the path of any part of the log, or of a log to start
    :param heading: the entry's heading, on one line, in the style of the
        log's earlier entries
    :param entry: the entry's text
    :returns: ``engram``, the path of the part written; ``part``, its
        number, 1 for the first; and ``version``, its new version
    :raises MethodError: ``unknown_space``, ``read_only``, ``not_a_log``
        for an engram that is not tagged ``log``, ``empty_heading``,
        ``entry_too_long`` for an entry that does not fit in an engram on its
        own, ``log_needs_rename`` for a full log with an old-style path,
        ``invalid_path``, ``protected_path`` or ``private_content``
    """
    heading = " ".join(heading.split())
    if not heading:
        raise MethodError("empty_heading")
    try:
        return await _log(caller, space, engram, heading, entry)
    except _Raced:
        # The other call has committed the log, so this time it is found.
        return await _log(caller, space, engram, heading, entry)


def _block(parts: list[Engram], heading: str, entry: str) -> str:
    """Write the text one entry adds at the end of a log.

    :param parts: the log's parts, first to last; none for a new log
    :param heading: the entry's heading, on one line
    :param entry: the entry's text
    :returns: a blank line, the heading at the log's entry level, a blank
        line and the text
    """
    marks = "#" * logs.entry_level(parts)
    heading = heading[: HEADING_CHARS - len(marks) - 1]
    return f"\n\n{marks} {heading}\n\n{entry}\n"


async def _log(
    caller: Caller, space: str, engram: str, heading: str, entry: str
) -> dict:
    """Add one entry to a log, in a transaction of its own.

    The entry's heading level is read from the parts once they are locked,
    so it follows the log as it is now.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param engram: the path of any part of the log, or of a log to start
    :param heading: the entry's heading, on one line
    :param entry: the entry's text
    :returns: what :func:`log` returns
    :raises _Raced: when another call created the log first
    :raises MethodError: as :func:`log` does
    """
    cap = get_settings().engrams.max_chars
    message = f"log: {heading[:60]}"
    with refusals():
        async with transaction_scope():
            await resolve_writable_space(caller.principal, space)
            path, parts = await _named_log(caller, space, engram)
            if not parts:
                block = _block([], heading, entry)
                return await _start_log(caller, space, path, block, message, cap)
            parts, last = await _lock_last(caller, space, parts)
            block = _block(parts, heading, entry)
            base = parts[0].path
            number = logs.part_number(base, last.path)
            body = last.body + block
            if not _leaves_room(body, base, number + 1, cap):
                return await _next_part(
                    caller, space, parts[0], last, block, message, cap
                )
            saved = await _save(
                caller,
                space,
                last.path,
                body,
                message=message,
                expected_version=last.version,
            )
            return _written(saved, number)


async def _named_log(
    caller: Caller, space: str, engram: str
) -> tuple[str, list[Engram]]:
    """Find the log a name refers to.

    The name is tried as given first, so a log stored before paths were
    normalized is still found, then normalized. A part that does not exist
    yet, ``<stem>-<n>.md``, refers to the log of ``<stem>.md`` when that is
    one.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param engram: the name the caller gave
    :returns: where to start the log when it does not exist yet, and its
        parts, first to last; no parts for a new log
    :raises MethodError: ``not_a_log`` when the name is an engram that is
        not a log; ``invalid_path`` when it cannot be normalized
    """
    parts = await _existing_log(caller, space, engram)
    if parts:
        return engram, parts
    with refusals(engram):
        path = normalize_path(engram)
    if path != engram:
        parts = await _existing_log(caller, space, path)
        if parts:
            return path, parts
    base = logs.base_path(path)
    if base is not None and (parts := await logs.series(caller, space, base)):
        return base, parts
    return path, []


async def _existing_log(caller: Caller, space: str, path: str) -> list[Engram]:
    """Find the parts of the log at a path, refusing an engram that is not one.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param path: the path to look at
    :returns: the parts, first to last, or none when nothing is there
    :raises MethodError: ``not_a_log`` when an engram is there but is not a log
    """
    parts = await logs.series(caller, space, path)
    if not parts and await get_engram(caller.principal, space, path) is not None:
        raise MethodError("not_a_log")
    return parts


async def _locked(engram: Engram) -> Engram | None:
    """Lock an engram's row until the transaction ends, and read it again.

    :param engram: the engram as read before
    :returns: the engram as it is now, or None when it is gone
    """
    return await Engram.objects().where(Engram.id == engram.id).lock_rows().first()


async def _lock_last(
    caller: Caller, space: str, parts: list[Engram]
) -> tuple[list[Engram], Engram]:
    """Lock a log for one entry: its first part, then its last.

    Every call to :func:`log` locks the first part before anything else, so
    calls on one log take turns. Once it is locked the parts are read again,
    since the call before may have started a new part. The last part is
    locked before its body is read, so no other write to it comes between.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param parts: the parts as read before, first to last
    :returns: the parts as they are now, and the last one, locked
    :raises MethodError: ``version_conflict`` when a part was deleted or
        stopped being a log in the meantime
    """
    first = await _locked(parts[0])
    parts = await logs.series(caller, space, parts[0].path)
    last = None
    if first is not None and parts:
        last = first if parts[-1].id == first.id else await _locked(parts[-1])
    if last is None:
        raise MethodError(
            "version_conflict",
            "This log changed while Reef was adding to it. Send the entry again.",
        )
    return parts, last


async def _start_log(
    caller: Caller, space: str, path: str, block: str, message: str, cap: int
) -> dict:
    """Create a log, tagged ``log``, with its first entry.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param path: where to create it
    :param block: the entry
    :param message: the revision message
    :param cap: the most characters an engram may hold
    :returns: what :func:`log` returns
    :raises _Raced: when another call created it first
    :raises MethodError: ``entry_too_long``, or when the write is refused
    """
    body = block.lstrip("\n")
    if not _leaves_room(body, path, 2, cap):
        raise _too_long(cap)
    try:
        saved = await _save(
            caller,
            space,
            path,
            body,
            message=message,
            tags=[logs.LOG_TAG],
            expected_version=0,
        )
    except UniqueViolationError:
        raise _Raced from None
    except MethodError as refused:
        if refused.code == "version_conflict":
            raise _Raced from None
        raise
    return _written(saved, 1)


async def _next_part(
    caller: Caller,
    space: str,
    first: Engram,
    last: Engram,
    block: str,
    message: str,
    cap: int,
) -> dict:
    """Start the next part of a full log with the entry, and link the two.

    The new part takes the next free number: a name already taken by an
    engram that is not part of this log is skipped, never overwritten. The
    closing link is left out only when the part before is too full for it,
    which happens only when something other than :func:`log` filled it.

    A log whose first part has an old-style path, stored before paths were
    normalized, cannot go on: its new part would be stored under the
    normalized name, which :func:`reef.methods.logs.series` does not find
    beside the old one. It is refused before anything is written.

    :param caller: who is calling
    :param space: the caller's name for the space
    :param first: the log's first part
    :param last: its last part, locked
    :param block: the entry
    :param message: the revision message
    :param cap: the most characters an engram may hold
    :returns: what :func:`log` returns, for the new part
    :raises MethodError: ``log_needs_rename`` for an old-style path,
        ``entry_too_long``, or when a write is refused
    """
    base = first.path
    if not _is_normalized(base):
        raise MethodError("log_needs_rename")
    number = logs.part_number(base, last.path) + 1
    while await Engram.exists().where(
        Engram.space_id == first.space_id,
        Engram.path == logs.part_path(base, number),
    ):
        number += 1
    path = logs.part_path(base, number)
    body = _opening(last.path) + block
    if not _leaves_room(body, base, number + 1, cap):
        raise _too_long(cap)
    saved = await _save(
        caller,
        space,
        path,
        body,
        message=message,
        title=f"{first.title} (part {number})",
        tags=[logs.LOG_TAG],
        expected_version=0,
    )
    closed = last.body + _closing(path)
    if len(closed) <= cap:
        await _save(
            caller,
            space,
            last.path,
            closed,
            message=f"log: continued in part {number}",
            expected_version=last.version,
            via=None,
        )
    return _written(saved, number)


def _is_normalized(path: str) -> bool:
    """Say whether a stored path is already in the form new paths take.

    :param path: the path as stored
    :returns: True when :func:`reef.engrams.normalize_path` leaves it as it is
    """
    try:
        return normalize_path(path) == path
    except InvalidPath:
        return False


# --- write -----------------------------------------------------------------------


def _problem(item: object) -> str | None:
    """Say what is wrong with one engram in a :func:`write` list, if anything.

    :param item: one entry of the list
    :returns: what is wrong, worded to follow "Engram n in the list", or None
    """
    if not isinstance(item, dict):
        return "is not an object"
    if not isinstance(item.get("path"), str) or not item["path"]:
        return "has no path"
    if not isinstance(item.get("body"), str):
        return "has no body"
    for field in ("title", "message"):
        if item.get(field) is not None and not isinstance(item[field], str):
            return f"has a {field} that is not text"
    tags = item.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags)
    ):
        return "has tags that are not a list of text"
    version = item.get("expected_version")
    if version is not None and (
        isinstance(version, bool) or not isinstance(version, int)
    ):
        return "has an expected_version that is not a whole number"
    return None


def _check_batch(engrams: list[dict]) -> None:
    """Refuse a malformed or oversize list before anything is written.

    :param engrams: the list :func:`write` was sent
    :raises MethodError: ``empty_batch``, ``batch_too_large``,
        ``malformed_item`` or ``use_instruct``
    """
    if not engrams:
        raise MethodError("empty_batch")
    if len(engrams) > MAX_BATCH:
        raise MethodError(
            "batch_too_large",
            f"Reef writes at most {MAX_BATCH} engrams in one call. Send them in "
            "several calls.",
        )
    for number, item in enumerate(engrams, start=1):
        if (problem := _problem(item)) is not None:
            raise MethodError(
                "malformed_item",
                f"Engram {number} in the list {problem}. {MESSAGES['malformed_item']}",
            )
    if any(_names_the_persona(item["path"]) for item in engrams):
        raise MethodError("use_instruct")


def _names_the_persona(path: str) -> bool:
    """Say whether a path, once normalized, is the persona's.

    :param path: the path as sent
    :returns: True for ``meta/persona.md`` in any spelling that normalizes
        to it
    """
    try:
        return normalize_path(path) == PERSONA_PATH
    except InvalidPath:
        return False


@method("write")
async def write(
    caller: Caller, reason: str, space: str, engrams: list[dict], message: str
) -> list[dict]:
    """Create or replace whole engrams, several at once, all or nothing.

    At most :data:`MAX_BATCH` engrams, each ``{path, body, title?, tags?,
    expected_version?, message?}``. Pass ``expected_version`` when replacing
    an engram, from when it was read.
    If any engram is refused, none is written. The persona is refused: it
    changes only through ``instruct``.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param space: the caller's name for the space
    :param engrams: the engrams to write
    :param message: why they change, for each engram without its own message
    :returns: one ``{engram, version}`` per engram written, in order
    :raises MethodError: ``use_instruct`` for the persona, ``empty_batch``,
        ``batch_too_large``, ``malformed_item``, ``unknown_space``,
        ``read_only``, ``version_conflict``, ``protected_path``,
        ``invalid_path``, ``engram_too_large`` or ``private_content``
    """
    _check_batch(engrams)
    try:
        with refusals():
            async with transaction_scope():
                saved = [
                    await _save(
                        caller,
                        space,
                        item["path"],
                        item["body"],
                        message=item.get("message") or message or "batch write",
                        title=item.get("title"),
                        tags=item.get("tags"),
                        expected_version=item.get("expected_version"),
                    )
                    for item in engrams
                ]
    except MethodError as refused:
        raise MethodError(
            refused.code, f"{refused.message} Nothing in this call was written."
        ) from None
    return [{"engram": engram.path, "version": engram.version} for engram in saved]


# --- store_file ------------------------------------------------------------------


@method("store_file")
async def store_file(
    caller: Caller,
    reason: str,
    space: str,
    filename: str,
    data_base64: str,
    mime: str,
    description: str,
    engram: str | None = None,
) -> dict:
    """Keep a document or an image, with a description to find it by.

    The description is what later conversations see and search, so it is
    required.

    :param caller: who is calling
    :param reason: why, in the caller's words
    :param space: the caller's name for the space
    :param filename: the file's own name, with its extension
    :param data_base64: the file's bytes, base64-encoded
    :param mime: its content type, such as ``application/pdf``
    :param description: what is in the file, concretely
    :param engram: the path of an engram in the same space the file belongs
        to; a path that names no engram attaches it to none
    :returns: ``space``, ``key``, ``ref`` (what :func:`reef.methods.find.open`
        takes), ``filename``, ``mime``, ``size`` and ``status``; with reading
        of files switched on, also ``text``: ``waiting`` when Reef will read
        the text inside the file, ``not_read`` when it does not read that
        kind
    :raises MethodError: ``invalid_filename``, ``invalid_mime``,
        ``description_required``, ``invalid_base64``, ``file_too_large``,
        ``unknown_space`` or ``read_only``
    """
    filename = filename.strip()
    if not filename or len(filename) > FILENAME_CHARS:
        raise MethodError("invalid_filename")
    mime = mime.strip()
    # Shape, not just length: the value is stored, sent to the object store
    # as the content type, and signed into a download URL.
    if len(mime) > MIME_CHARS or not MIME_RE.fullmatch(mime):
        raise MethodError("invalid_mime")
    if not description.strip():
        raise MethodError("description_required")
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError):
        raise MethodError("invalid_base64") from None
    limit = get_settings().files.max_bytes
    if len(data) > limit:
        raise MethodError(
            "file_too_large",
            f"{MESSAGES['file_too_large']} Reef keeps files of at most "
            f"{limit:,} bytes.",
        )
    with refusals():
        attachment = await add_attachment(
            caller.principal,
            space,
            data,
            mime,
            filename=filename,
            description=description,
            store=S3ObjectStore(),
            engram_path=engram,
        )
    key = attachment.object_key
    stored = {
        "space": space,
        "key": key,
        "ref": f"{space}/{FILE_PREFIX}{key}",
        "filename": attachment.filename,
        "mime": attachment.mime,
        "size": attachment.byte_size,
        "status": attachment.status,
    }
    if get_settings().reading.enabled and attachment.text_status is not None:
        stored["text"] = attachment.text_status
    return stored
