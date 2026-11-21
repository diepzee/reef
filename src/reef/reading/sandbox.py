"""Run a file's parser in a child process that holds nothing worth stealing.

Uploaded files are untrusted, and parsers have bugs. So the worker never
parses a file itself. It starts :mod:`reef.reading.child`, writes the bytes
to its stdin and reads JSON from its stdout, and a PNG's raw bytes after it
when the answer holds one. The child:

- gets an environment of three variables, so no storage key, database URL
  or API key of the worker reaches it;
- runs Python isolated (``-I``: no ``PYTHON*`` variables, no user site, no
  current directory on the path) and writes no bytecode (``-B``);
- runs as the unprivileged ``reader`` user, with its group only, when the
  worker runs as root;
- caps its own memory, CPU time, open files and core dumps before it reads
  a byte (:func:`reef.reading.child.apply_limits`);
- runs in a session of its own, so its whole process group can be killed
  once the read ends, however it ended;
- has its stderr thrown away, and is heard on stdout only up to a bound
  derived from its operation and the limits, never from what the file
  declares (see :func:`run_child`).

A process that left the group still runs as ``reader``. A root worker on
Linux kills every process of ``reader`` before and after each reading job
(:func:`kill_reader_processes`).

A failure comes back as a :class:`~reef.reading.ReadFailed` class and
nothing else. Whatever the child printed stays out of every exception, log
line and span: it may be the file's own text.
"""

import asyncio
import contextlib
import json
import math
import os
import pwd
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from reef.config import get_settings
from reef.reading import (
    ChildCrashed,
    Encrypted,
    FileKind,
    NoMainPart,
    ReadFailed,
    ReadTimedOut,
    SandboxUnavailable,
    TooLarge,
    Unreadable,
)
from reef.reading.pages import MAX_PIXELS, MIN_SCAN_DPI, MIN_SIDE, PNG_SIGNATURE

READER_USER = "reader"
"""The unprivileged user a root worker runs the child as. The Docker image
creates it."""

DEFAULT_ANSWER_BYTES = 16 * 1024 * 1024
"""The most an operation without a bound of its own may print. Parsing JSON
takes many times its size in memory, so an operation that answers with
more must declare a bound derived from its input."""

ANSWER_SLACK = 64 * 1024
"""Room for the JSON around an answer, and for an error. Also the most the
JSON line before a PNG may take."""

MOST_PAGES = 2**31 - 1
"""The most pages a child may say a file has: what the ``pages`` column
holds."""

_COUNTS_TO_ONE_MORE = frozenset({FileKind.DOCX, FileKind.XLSX, FileKind.PPTX})
"""The text formats whose count of parts stops at one past the most read."""

PROC = Path("/proc")
"""Where Linux lists its processes."""

_SWEEPS = 100
"""How many times a sweep looks again for processes started while it looked."""

_CHUNK_BYTES = 64 * 1024

_FAILURES: dict[str, type[ReadFailed]] = {
    failure.__name__: failure
    for failure in (
        Unreadable,
        NoMainPart,
        Encrypted,
        TooLarge,
        ReadTimedOut,
        ChildCrashed,
    )
}
"""The failures a child may name. Any other name counts as a crash."""


def parts_answer_bytes(part_chars: int, max_parts: int) -> int:
    """Return the most the ``parts`` operation can print.

    The answer holds at most ``max_parts`` parts of at most ``part_chars``
    characters. A character is at most four bytes in UTF-8, and JSON escaping
    at most doubles one that stays a byte. Each part adds a quote pair and a
    separator. So the bound depends on the limits, never on the file: no
    file, however it unpacks, makes the worker read more.

    :param part_chars: the most characters in one part
    :param max_parts: the most parts in the answer
    :returns: the bound, in bytes
    """
    return max_parts * (4 * part_chars + 4) + ANSWER_SLACK


def _is_parts(answer: dict, args: list[str]) -> bool:
    """Tell whether an answer is what ``parts`` gives.

    :param answer: the child's answer
    :param args: ``parts``, the kind, the part size and the most parts
    :returns: True for ``{"parts": [str, ...], "pages": int}`` with no more
        than the most parts, and ``pages`` at least as many as are given and
        at most :data:`MOST_PAGES`; for Word, Excel and PowerPoint, at most
        one more than the most parts
    """
    parts = answer.get("parts")
    pages = answer.get("pages")
    most = MOST_PAGES
    if args[1] in _COUNTS_TO_ONE_MORE:
        most = min(most, int(args[3]) + 1)
    return (
        answer.keys() == {"parts", "pages"}
        and isinstance(parts, list)
        and len(parts) <= int(args[3])
        and all(isinstance(part, str) for part in parts)
        and _is_count(pages)
        and len(parts) <= pages <= most
    )


def text_answer_bytes(chars: int) -> int:
    """Return the most an answer holding one page of text can print.

    The same reckoning as :func:`parts_answer_bytes`, for one part. Room for
    a confidence or a resolution is in the slack.

    :param chars: the most characters in the text
    :returns: the bound, in bytes
    """
    return parts_answer_bytes(chars, 1)


def png_answer_bytes(pixels: int) -> int:
    """Return the most an answer holding one page's PNG can print.

    The PNG is greyscale, a byte a pixel, and each row adds a filter byte:
    at worst, with rows :data:`~reef.reading.pages.MIN_SIDE` pixels wide,
    1/64 more. Deflate and the PNG's chunks add well under 0.1%; 0.2% is
    allowed. The PNG travels as raw bytes after a one-line JSON header, which
    the slack holds. So the bound follows from the pixel cap, never from the
    file: about 41 MB for a page of 40 million pixels.

    :param pixels: the most pixels in the page
    :returns: the bound, in bytes
    """
    png = pixels + pixels // MIN_SIDE + pixels // 500 + ANSWER_SLACK
    return png + ANSWER_SLACK


def _is_count(value: object) -> bool:
    """Tell whether a value is a whole number, and not a truth value.

    :param value: from the child's answer
    :returns: True for an ``int`` that is not a ``bool``
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value: object, chars: int) -> bool:
    """Tell whether a value is a text no longer than allowed.

    :param value: from the child's answer
    :param chars: the most characters
    :returns: True for a ``str`` of at most ``chars`` characters
    """
    return isinstance(value, str) and len(value) <= chars


def _is_survey(answer: dict, args: list[str]) -> bool:
    """Tell whether an answer is what ``survey`` gives.

    :param answer: the child's answer
    :param args: ``survey``, the kind and the most pages that will be read
    :returns: True for ``{"pages": int}``, from one page to
        :data:`MOST_PAGES`; for an image, at most one more than will be read
    """
    if answer.keys() != {"pages"} or not _is_count(answer["pages"]):
        return False
    most = MOST_PAGES
    if args[1] == FileKind.IMAGE:
        most = min(most, int(args[2]) + 1)
    return 1 <= answer["pages"] <= most


def _is_png(value: object) -> bool:
    """Tell whether a value is a PNG's bytes.

    :param value: from the child's answer, after :func:`_answer` has taken
        the bytes that followed it
    :returns: True for ``bytes`` that start with the PNG signature
    """
    return isinstance(value, bytes) and value.startswith(PNG_SIGNATURE)


def _is_page(answer: dict, args: list[str]) -> bool:
    """Tell whether an answer is what ``page`` gives.

    :param answer: the child's answer
    :param args: ``page``, the number, the resolution and the most characters
    :returns: True for ``{"text": str}`` within the most characters, or for
        a PNG with the resolution it was rendered at: the one asked for, or
        a lower one down to :data:`~reef.reading.pages.MIN_SCAN_DPI`
    """
    if answer.keys() == {"text"}:
        return _is_text(answer["text"], int(args[3]))
    if answer.keys() != {"png", "dpi"} or not _is_count(answer["dpi"]):
        return False
    asked = int(args[2])
    return _is_png(answer["png"]) and min(MIN_SCAN_DPI, asked) <= answer["dpi"] <= asked


def _is_frame(answer: dict, args: list[str]) -> bool:
    """Tell whether an answer is what ``frame`` gives.

    :param answer: the child's answer
    :param args: ``frame`` and the number
    :returns: True for ``{"png": bytes}``
    """
    return answer.keys() == {"png"} and _is_png(answer["png"])


def _is_confidence(value: object) -> bool:
    """Tell whether a value is a confidence, or none.

    :param value: from the child's answer
    :returns: True for None, or a finite number from 0 to 1
    """
    if value is None:
        return True
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _is_ocr(answer: dict, args: list[str]) -> bool:
    """Tell whether an answer is what ``ocr`` gives.

    :param answer: the child's answer
    :param args: ``ocr``, the program, the languages, the resolution and the
        most characters
    :returns: True for a text within the most characters and a confidence
    """
    return (
        answer.keys() == {"text", "confidence"}
        and _is_text(answer["text"], int(args[4]))
        and _is_confidence(answer["confidence"])
    )


class _Operation(NamedTuple):
    """What one operation may print, and what its answer looks like."""

    answer_bytes: Callable[[list[str]], int]
    """The most it may print in all, from its arguments and the limits."""
    shaped: Callable[[dict, list[str]], bool]
    """The check of its answer's shape."""
    json_bytes: Callable[[list[str]], int] | None = None
    """The most its JSON may take when no PNG follows it; None when the
    answer is JSON alone, bounded by :attr:`answer_bytes`. Parsing takes many
    times its input in memory, so an answer that may hold a PNG bounds its
    JSON on its own."""


_OPERATIONS: dict[str, _Operation] = {
    "parts": _Operation(
        lambda args: parts_answer_bytes(int(args[2]), int(args[3])), _is_parts
    ),
    "survey": _Operation(lambda args: ANSWER_SLACK, _is_survey),
    "page": _Operation(
        lambda args: max(text_answer_bytes(int(args[3])), png_answer_bytes(MAX_PIXELS)),
        _is_page,
        lambda args: text_answer_bytes(int(args[3])),
    ),
    "frame": _Operation(
        lambda args: png_answer_bytes(MAX_PIXELS), _is_frame, lambda args: ANSWER_SLACK
    ),
    "ocr": _Operation(lambda args: text_answer_bytes(int(args[4])), _is_ocr),
}
"""Each operation's bounds, derived from its arguments and the limits, and
the check of its answer's shape. An operation not here gets
:data:`DEFAULT_ANSWER_BYTES` and no check: every operation a later reader
adds belongs here."""


def child_command() -> list[str]:
    """Return the command that starts the child, without its arguments.

    :returns: this Python, isolated and writing no bytecode, running
        :mod:`reef.reading.child`
    """
    return [sys.executable, "-I", "-B", "-m", "reef.reading.child"]


def child_environment() -> dict[str, str]:
    """Return the child's whole environment.

    Built from nothing, never copied from the worker's.

    :returns: a fixed search path, a UTF-8 locale, and one OpenMP thread
    """
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OMP_THREAD_LIMIT": "1"}


def child_user() -> str | None:
    """Tell which user the child runs as.

    :returns: ``reader`` when the worker runs as root; None otherwise, and
        the child runs as the worker's own user
    :raises SandboxUnavailable: when the worker runs as root and ``reader``
        does not exist: a root child would undo the sandbox
    """
    if os.geteuid() != 0:
        return None
    try:
        pwd.getpwnam(READER_USER)
    except KeyError:
        raise SandboxUnavailable from None
    return READER_USER


def _identity() -> dict[str, Any]:
    """Return the user options for starting the child.

    :returns: the user, group and supplementary groups to switch to, or
        nothing when the worker is not root
    :raises SandboxUnavailable: when the worker is root and ``reader`` is missing
    """
    user = child_user()
    if user is None:
        return {}
    entry = pwd.getpwnam(user)
    # Without the group and an empty group list, the child would keep
    # root's group and every supplementary group of the worker.
    return {"user": entry.pw_uid, "group": entry.pw_gid, "extra_groups": []}


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """Kill the child and every process it started that stayed in its group.

    :param process: the child, leader of its own process group
    """
    # The group is gone already, or only zombies are left in it, which
    # macOS answers with a refusal.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


def kill_reader_processes() -> int:
    """Kill every process of the ``reader`` user.

    A child that runs code from a hostile file can start a process that
    outlives its read. One that stays in the child's process group dies with
    it; one that left the group (``setsid``) does not. It would run as
    ``reader``, like every later child, so it could read the files they are
    given through their ``/proc/<pid>/fd``.

    The worker calls this at the start and at the end of each reading job.
    It runs one job at a time, so no read is in flight then, and every
    process of ``reader`` is one left behind. Nothing else in the worker's
    container may run as ``reader`` meanwhile: a timing run of
    ``scripts/file_report.py`` beside a reading job loses its read.

    Linux only, and only when the worker runs as root: elsewhere the child
    runs as the worker's own user, and nothing tells its processes from the
    worker's. It reads ``/proc`` and signals with the standard library.

    :returns: how many processes were killed
    """
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        return 0
    try:
        uid = pwd.getpwnam(READER_USER).pw_uid
    except KeyError:
        return 0
    return kill_owned_by(uid, PROC)


def kill_owned_by(uid: int, proc: Path) -> int:
    """Kill every live process of one user, then look again until none is left.

    A process is the user's when any of its user ids is: real, effective,
    saved or file system. So a setuid program the user started counts too. A
    zombie has ended already and is passed over. After each round of kills
    the sweep looks again, for processes forked while it looked, at most
    :data:`_SWEEPS` times.

    :param uid: the user
    :param proc: where the processes are listed, ``/proc`` on Linux
    :returns: how many processes were killed
    """
    killed: set[int] = set()
    for _ in range(_SWEEPS):
        found = [pid for pid in _owned_by(uid, proc) if pid not in killed]
        if not found:
            break
        for pid in found:
            # It may have ended since it was listed.
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        killed.update(found)
    return len(killed)


def _owned_by(uid: int, proc: Path) -> list[int]:
    """List the live processes of one user.

    The kernel escapes a line break in a process's name, so no process can
    write a line of its status that reads as another field.

    :param uid: the user
    :param proc: where the processes are listed
    :returns: their ids, lowest first
    """
    owned: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            status = (entry / "status").read_text(errors="replace")
        except OSError:
            # It ended while the sweep looked.
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        state = fields.get("State", "").strip()[:1]
        if state not in ("Z", "X") and str(uid) in fields.get("Uid", "").split():
            owned.append(int(entry.name))
    return sorted(owned)


async def run_child(
    args: list[str],
    data: bytes,
    *,
    seconds: float,
    answer_bytes: int | None = None,
) -> dict:
    """Run one operation in the child and return its answer.

    The answer is read only up to a bound, and parsed only within it: a
    child that gets code from a hostile file must not cost the worker its
    memory. Each operation needs a bound derived from its input and the
    limits, declared in ``_OPERATIONS`` or passed as ``answer_bytes``, with
    its answer's shape: ``parts`` (:func:`parts_answer_bytes`), ``survey``,
    ``page``, ``frame`` (:func:`png_answer_bytes`) and ``ocr``
    (:func:`text_answer_bytes`). The JSON is bounded on its own: the line
    before a PNG to :data:`ANSWER_SLACK`, a page's text to its text's bound.
    A PNG that follows the JSON is checked against the length the JSON
    gives, and handed back as ``bytes``. When the read ends, however it
    ends, the child's process group is killed.

    :param args: the operation and its operands, as
        :func:`reef.reading.child.main` takes them after the limits
    :param data: the bytes the child reads from stdin
    :param seconds: the longest the child may run, wall clock; its CPU time
        is capped at this, rounded up
    :param answer_bytes: the most the child may print; by default the
        operation's own bound, else :data:`DEFAULT_ANSWER_BYTES`
    :returns: the child's answer, a JSON object without an ``error`` key,
        in the operation's shape; its ``png``, if any, is the PNG's bytes
    :raises ReadTimedOut: when the child runs out of time
    :raises ReadFailed: the class the child named, when it named one
    :raises ChildCrashed: when the child prints more than its bound, or
        ends without an answer of the right shape
    :raises SandboxUnavailable: when the child cannot be started safely
    """
    operation = _OPERATIONS.get(args[0])
    if answer_bytes is None:
        answer_bytes = (
            DEFAULT_ANSWER_BYTES if operation is None else operation.answer_bytes(args)
        )
    json_bytes = answer_bytes
    if operation is not None and operation.json_bytes is not None:
        json_bytes = min(json_bytes, operation.json_bytes(args))
    command = [
        *child_command(),
        "--memory-mb",
        str(get_settings().reading.memory_mb),
        "--cpu-seconds",
        str(math.ceil(seconds)),
        *args,
    ]
    identity = _identity()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=child_environment(),
            cwd="/",
            start_new_session=True,
            **identity,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SandboxUnavailable from error
    try:
        async with asyncio.timeout(seconds):
            stdout = await _exchange(process, data, answer_bytes)
    except TimeoutError:
        raise ReadTimedOut from None
    finally:
        # However the read ended. A child that timed out, was cancelled or
        # talked too much may be gone already while a process it started
        # holds the pipe open; one that answered may have left a process
        # running. The group goes, whatever the child's own state.
        _kill_group(process)
        await process.wait()
    answer = _answer(process.returncode, stdout, json_bytes)
    if operation is not None and not operation.shaped(answer, args):
        raise ChildCrashed
    return answer


async def _feed(stdin: asyncio.StreamWriter, data: bytes) -> None:
    """Write the file to the child and close its stdin.

    :param stdin: the child's stdin
    :param data: the file's bytes
    """
    # A child may end without reading all of it; that is for its answer
    # to tell, as in ``Process.communicate``.
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        stdin.write(data)
        await stdin.drain()
    stdin.close()


async def _exchange(
    process: asyncio.subprocess.Process, data: bytes, limit: int
) -> bytearray:
    """Give the child the file and take its answer, as ``communicate`` would.

    Unlike ``communicate``, it reads no more than one byte past ``limit``,
    into one buffer that is not copied again.

    :param process: the child, started with pipes for stdin and stdout
    :param data: the file's bytes
    :param limit: the most the child may print
    :returns: everything the child printed, once it has ended
    :raises ChildCrashed: when the child prints more than ``limit``
    """
    assert process.stdin is not None and process.stdout is not None
    feeder = asyncio.create_task(_feed(process.stdin, data))
    try:
        printed = bytearray()
        while chunk := await process.stdout.read(
            min(_CHUNK_BYTES, limit + 1 - len(printed))
        ):
            printed += chunk
            if len(printed) > limit:
                raise ChildCrashed
        await feeder
        await process.wait()
    finally:
        feeder.cancel()
    return printed


def _answer(returncode: int | None, stdout: bytearray, json_bytes: int) -> dict:
    """Turn what the child left behind into its answer or a failure.

    The answer is one line of JSON. When it holds a ``png``, that is the
    length of the raw bytes after the line, which must be exactly that long;
    they replace it. Anything else after the line is a crash. JSON longer
    than it can be is a crash too, and is never parsed: the line before
    raw bytes may take :data:`ANSWER_SLACK`, an answer of JSON alone, with
    or without a line break at its end, ``json_bytes``.

    Raised outside any ``except`` block, so no failure carries the parse
    error, and with it the child's output, as its context.

    :param returncode: how the child ended
    :param stdout: everything it printed
    :param json_bytes: the most an answer without raw bytes may take
    :returns: its answer
    :raises ReadTimedOut: when it ran out of CPU time
    :raises ReadFailed: the class it named
    :raises ChildCrashed: for anything else that is not an answer
    """
    if returncode == -signal.SIGXCPU:
        raise ReadTimedOut
    if returncode != 0:
        raise ChildCrashed
    end = stdout.find(b"\n")
    rest = 0 if end < 0 else len(stdout) - end - 1
    json_end = len(stdout) if end < 0 else end
    # Only a PNG's header has bytes after it, and a header is short.
    if json_end > (ANSWER_SLACK if rest else json_bytes):
        raise ChildCrashed
    # A long JSON answer, such as ``parts``, has no newline: parsed in place.
    head = stdout if end < 0 else stdout[:end]
    try:
        answer = json.loads(head)
    except (ValueError, RecursionError):
        answer = None
    del head
    if not isinstance(answer, dict):
        raise ChildCrashed
    if "error" in answer:
        raise _FAILURES.get(str(answer["error"]), ChildCrashed)
    if "png" in answer:
        if end < 0 or not _is_count(answer["png"]) or answer["png"] != rest:
            raise ChildCrashed
        with memoryview(stdout) as view:
            answer["png"] = bytes(view[end + 1 :])
    elif rest:
        raise ChildCrashed
    return answer
