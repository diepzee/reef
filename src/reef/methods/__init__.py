"""The method layer: what an assistant or a person can ask Reef to do.

Each method is one async function, independent of transport. It takes a
:class:`Caller`, the ``reason`` and its own arguments, and returns plain data.
The MCP tools are thin shells over these functions; HTTP shells can follow.

**What the wrapper does.** :func:`method` registers a method under its name
in :data:`REGISTRY`, cuts the reason to
:data:`~reef.rls.CALL_LOG_MAX_REASON` characters, times the call and records
it in the private call log (:func:`reef.calllog.record`), whether the method
returned or raised. A method that raised is recorded with an outcome that
names the failure (see :func:`outcome_of`), and the error is raised again
unchanged.

**Transactions are the method's own.** A method opens the short
transactions it needs, armed for the caller, as
:func:`reef.search.search_engrams` does, so model calls can happen outside
them. The wrapper opens none around the method. It records after the method
has finished, in a transaction of its own, so the row of a failed call is
not rolled back with the method's work.

**The reason stays private.** The wrapper hands it to the method and to the
call log, and to nothing else: no log line and no telemetry attribute here
carries it.
"""

import functools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Concatenate, Literal

from reef import calllog
from reef.access import Principal
from reef.limits import FILENAME_CHARS
from reef.rls import CALL_LOG_MAX_REASON, WRITER_KINDS

type Writer = Literal["assistant", "person", "system"]
"""Who made a call: an assistant over MCP, a person on the website, or the
system. The same values as :data:`reef.rls.WRITER_KINDS`."""

type Method[**P, R: dict | list] = Callable[Concatenate[Caller, str, P], Awaitable[R]]
"""A method: it takes the caller, the reason and its own arguments."""

REGISTRY: dict[str, Method] = {}
"""Every method by its name, as wrapped by :func:`method`."""

OK = "ok"
"""The outcome recorded for a call that returned."""

MESSAGES = {
    "relation_invalid": "Write each relation as wiki links, such as about: [[house.md]]. Use each key once.",
    "relation_block_in_body": "The engram's text starts with a block of relation lines. Rewrite the engram with write, which reads the block.",
    "relation_too_many": "A relation key may name at most 20 engrams. Split this list.",
    "relation_target_missing": "This link names no engram the person can read. Check its space and path.",
    "relation_into_personal": "An engram in a shared space cannot link into a personal space.",
    "relation_to_itself": "An engram cannot link to itself. Name another engram.",
    "relation_needs_write": "Replacing an engram needs write access to its space. The person can only read it.",
    "relation_needs_confirmation": "Replacing an engram needs the person's yes. Use write's two calls.",
    "relation_circle": "These replacements would lead back to the same engram. Remove the link that closes the circle.",
    "stale_token": (
        "This confirmation has expired or does not match the request. "
        "Make the first call again to get a new one."
    ),
    "not_configured": (
        "Reef cannot confirm actions right now, because it is not set up for "
        "it. Tell the person; their Reef's operator needs to fix this."
    ),
    "invalid_as_of": (
        "This moment is not a date Reef can read. Send as_of as an ISO 8601 "
        "date and time, such as 2026-03-01T12:00:00, or leave it out to read "
        "the engram as it is now."
    ),
    "invalid_since": (
        "This moment is not a date Reef can read. Send since as an ISO 8601 "
        "date and time, such as 2026-03-01T12:00:00, or leave it out for the "
        "last 7 days."
    ),
    "unknown_space": (
        "Reef has no space by that name for this person. Call load_index to "
        "see their spaces, then use one of those names."
    ),
    "read_only": (
        "The person can read this space but not change it: only its full "
        "members can. Tell the person, and ask where to keep this instead."
    ),
    "version_conflict": (
        "This engram changed after you read it. Open it again, then send "
        "your change based on what it says now."
    ),
    "protected_path": (
        "Engrams under meta/ belong to Reef and cannot be written here. To "
        "change how assistants work with the person, use instruct."
    ),
    "use_instruct": (
        "The persona changes only through instruct, which shows the person "
        "the new text before it is saved. Send it there, not to write."
    ),
    "invalid_path": (
        "Reef cannot use this path. Use lower-case letters, digits, '-', '_', "
        "'.' and '/', with no empty or '..' folder and no '/' at the start."
    ),
    "engram_too_large": (
        "This text is too long for one engram. Split it into several engrams."
    ),
    "private_content": (
        "This would copy text from the person's personal space into a shared "
        "space. Use share for that: it shows the person what others will see. "
        "If the words only match by chance, write them differently."
    ),
    "not_a_log": (
        "This engram is not a log, so Reef does not add entries to it. Name "
        "a log, or a new engram to start one. To change this engram, use "
        "write or correct."
    ),
    "empty_heading": (
        "An entry needs a heading. Send a short one in the style of the "
        "log's earlier entries, such as the date."
    ),
    "entry_too_long": (
        "This entry is too long for one engram, even on its own. Split it "
        "into shorter entries, or keep the text as a file with store_file."
    ),
    "log_needs_rename": (
        "This log is full, and its name is in an old style Reef no longer "
        "uses, so it cannot go on in a second part. Tell the person, and ask "
        "whether to start a new log under a name in lower-case letters, "
        "digits and '-'. Then send the entry there."
    ),
    "empty_batch": "The list of engrams is empty. Send at least one to write.",
    "batch_too_large": (
        "This is more engrams than Reef writes in one call. Send them in several calls."
    ),
    "malformed_item": (
        "Each engram in the list needs a path and a body, as text. A title "
        "and a message are text, tags a list of text, and expected_version a "
        "whole number."
    ),
    "invalid_filename": (
        f"The file name is empty or longer than {FILENAME_CHARS} characters. Send the "
        "file's own name, with its extension."
    ),
    "invalid_mime": (
        "Reef cannot read this content type. Send it as type/subtype, such "
        "as application/pdf or image/png."
    ),
    "description_required": (
        "A file needs a description: later conversations find and understand "
        "the file through it. Say concretely what is in the file."
    ),
    "invalid_base64": (
        "The file's data is not valid base64. Encode the file's bytes as "
        "base64 and send them again."
    ),
    "file_too_large": (
        "This file is larger than Reef keeps. Send a smaller file, or only "
        "the part that matters."
    ),
    "not_found": (
        "Reef has nothing there that this person can read. Check the path or "
        "key, for example with recall, and send it again."
    ),
    "one_target": (
        "Name exactly one thing to forget: an engram, by its path, or a "
        "file, by its key. Send one of them."
    ),
    "passage_not_found": (
        "The engram does not contain this passage. Open the engram, copy "
        "the passage exactly as it is written, and send it again."
    ),
    "passage_ambiguous": (
        "This passage occurs more than once. Send a longer passage that "
        "occurs only once, with some of the words around it."
    ),
    "nothing_to_undo": (
        "There is no earlier version to go back to. The engram is at its "
        "first version, or you named the version it is at now."
    ),
    "share_to_personal": (
        "Sharing moves content out of the personal space. Name a shared "
        "space as to_space; load_index lists the person's spaces."
    ),
    "share_needs_path": (
        "A section becomes an engram of its own, and its name is a choice "
        "for the person. Ask what to call it, then send it as to_path."
    ),
    "share_target_exists": (
        "An engram already exists at that path in the shared space. Make the "
        "first call again with another to_path."
    ),
    "no_such_version": (
        "The engram has no such version. Versions count from 1 up to the "
        "current one. Open the engram to see its current version."
    ),
}
"""The plain message for each error code that has a standard one."""


@dataclass(frozen=True)
class Caller:
    """Who is calling a method, and through which channel."""

    principal: Principal
    """The person on whose behalf the call is made."""

    writer: Writer
    """Who made the call: ``assistant``, ``person`` or ``system``."""

    session_id: str | None
    """A label for the conversation, or None."""

    def __post_init__(self) -> None:
        """Refuse a writer the call log would refuse.

        The type says which values are allowed, but nothing checks a type at
        run time; the database would, only later, and by logging the row.

        :raises ValueError: when the writer is not a known kind
        """
        if self.writer not in WRITER_KINDS:
            raise ValueError(f"unknown writer {self.writer!r}")


class MethodError(Exception):
    """A method refused, for a reason the assistant can act on.

    ``code`` is stable, for programs; ``message`` is plain language, for the
    assistant to read and pass on.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        """Build the error.

        :param code: a stable code, such as ``stale_token``
        :param message: what to tell the assistant; defaults to the standard
            message for the code in :data:`MESSAGES`, else to the code
        """
        self.code = code
        self.message = message or MESSAGES.get(code, code)
        super().__init__(self.message)


def method[**P, R: dict | list](
    name: str,
) -> Callable[[Method[P, R]], Method[P, R]]:
    """Make an async function a method: registered, timed and logged.

    The function must take the caller and the reason first. The reason it
    receives is already cut to :data:`~reef.rls.CALL_LOG_MAX_REASON`
    characters. The function is registered as wrapped, so a call through
    :data:`REGISTRY` is logged too.

    A cancelled call is not recorded: once cancelled, nothing awaited
    afterwards can be relied on to finish.

    :param name: the method's name, as assistants see it
    :returns: the decorator
    :raises ValueError: when a method of that name is already registered
    """

    def decorate(fn: Method[P, R]) -> Method[P, R]:
        """Wrap and register one method.

        :param fn: the method
        :returns: the wrapped method
        """
        if name in REGISTRY:
            raise ValueError(f"method {name!r} is registered twice")

        @functools.wraps(fn)
        async def wrapper(
            caller: Caller, reason: str, *args: P.args, **kwargs: P.kwargs
        ) -> R:
            """Run the method, then record the call.

            :param caller: who is calling
            :param reason: why, in the caller's words
            :param args: the method's own positional arguments
            :param kwargs: the method's own keyword arguments
            :returns: what the method returned
            """
            reason = reason[:CALL_LOG_MAX_REASON]
            started = time.perf_counter()
            try:
                result = await fn(caller, reason, *args, **kwargs)
            except Exception as error:
                await _record(caller, name, reason, started, 0, outcome_of(error))
                raise
            await _record(caller, name, reason, started, _count(result), OK)
            return result

        REGISTRY[name] = wrapper
        return wrapper

    return decorate


def outcome_of(error: Exception) -> str:
    """Name a failure for the call log.

    A :class:`MethodError` is named by its code, such as ``stale_token``, so
    the log tells one refusal from another. Any other error is named by its
    class.

    :param error: the error that ended the call
    :returns: the outcome to record
    """
    if isinstance(error, MethodError):
        return error.code
    return type(error).__name__


def _count(result: dict | list) -> int:
    """Count what a call returned, for the call log.

    A list counts its items. A dict with exactly one list among its values
    counts that list's items: it is the dict's main content, as in
    ``{"engrams": [...]}``. Any other result counts as 1.

    :param result: what the method returned
    :returns: how many items it returned
    """
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        lists = [value for value in result.values() if isinstance(value, list)]
        if len(lists) == 1:
            return len(lists[0])
    return 1


async def _record(
    caller: Caller,
    name: str,
    reason: str,
    started: float,
    count: int,
    outcome: str,
) -> None:
    """Record one finished call in the call log.

    :param caller: who called
    :param name: the method's name
    :param reason: why, already cut
    :param started: :func:`time.perf_counter` when the call began
    :param count: how many items the call returned
    :param outcome: :data:`OK`, or what :func:`outcome_of` names the failure
    """
    await calllog.record(
        caller.principal,
        session_id=caller.session_id,
        writer=caller.writer,
        method=name,
        reason=reason,
        duration_ms=round((time.perf_counter() - started) * 1000),
        returned=count,
        outcome=outcome,
    )
