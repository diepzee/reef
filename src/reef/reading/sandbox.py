"""Run a file's parser in a child process that holds nothing worth stealing.

Uploaded files are untrusted, and parsers have bugs. So the worker never
parses a file itself. It starts :mod:`reef.reading.child`, writes the bytes
to its stdin and reads JSON from its stdout. The child:

- gets an environment of three variables, so no storage key, database URL
  or API key of the worker reaches it;
- runs Python isolated (``-I``: no ``PYTHON*`` variables, no user site, no
  current directory on the path) and writes no bytecode (``-B``);
- runs as the unprivileged ``reader`` user, with its group only, when the
  worker runs as root;
- caps its own memory, CPU time, open files and core dumps before it reads
  a byte (:func:`reef.reading.child.apply_limits`);
- runs in a session of its own, so the whole process group can be killed
  when it takes too long;
- has its stderr thrown away, and is heard on stdout only up to a bound
  derived from its operation and the limits, never from what the file
  declares (see :func:`run_child`).

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
from typing import Any

from reef.config import get_settings
from reef.reading import (
    ChildCrashed,
    Encrypted,
    ReadFailed,
    ReadTimedOut,
    SandboxUnavailable,
    TooLarge,
    Unreadable,
)
from reef.reading.pages import MAX_PIXELS, MODEL_CHARS

READER_USER = "reader"
"""The unprivileged user a root worker runs the child as. The Docker image
creates it."""

DEFAULT_ANSWER_BYTES = 16 * 1024 * 1024
"""The most an operation without a bound of its own may print. Parsing JSON
takes many times its size in memory, so an operation that answers with
more must declare a bound derived from its input."""

ANSWER_SLACK = 64 * 1024
"""Room for the JSON around an answer, and for an error."""

_CHUNK_BYTES = 64 * 1024

_FAILURES: dict[str, type[ReadFailed]] = {
    failure.__name__: failure
    for failure in (Unreadable, Encrypted, TooLarge, ReadTimedOut, ChildCrashed)
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
        than the most parts, and ``pages`` at least as many as are given
    """
    parts = answer.get("parts")
    pages = answer.get("pages")
    return (
        answer.keys() == {"parts", "pages"}
        and isinstance(parts, list)
        and len(parts) <= int(args[3])
        and all(isinstance(part, str) for part in parts)
        and isinstance(pages, int)
        and not isinstance(pages, bool)
        and pages >= len(parts)
    )


def text_answer_bytes(chars: int) -> int:
    """Return the most an answer holding one page of text can print.

    The same reckoning as :func:`parts_answer_bytes`, for one part. Room for
    a confidence and a model's name is in the slack.

    :param chars: the most characters in the text
    :returns: the bound, in bytes
    """
    return parts_answer_bytes(chars, 1)


def png_answer_bytes(pixels: int) -> int:
    """Return the most an answer holding one page's PNG can print.

    The PNG is greyscale, a byte a pixel, and each row adds a filter byte:
    at worst, a row one pixel wide, twice the pixels. Deflate and the PNG's
    chunks add well under 1% to that, and base64 makes four bytes of three.
    So the bound follows from the pixel cap, never from the file.

    :param pixels: the most pixels in the page
    :returns: the bound, in bytes
    """
    raw = 2 * pixels
    png = raw + raw // 100 + ANSWER_SLACK
    return 4 * math.ceil(png / 3) + ANSWER_SLACK


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
    :param args: ``survey`` and the kind
    :returns: True for ``{"pages": int}``, at least one page
    """
    return (
        answer.keys() == {"pages"}
        and _is_count(answer["pages"])
        and answer["pages"] >= 1
    )


def _is_png(answer: dict) -> bool:
    """Tell whether an answer is one page's PNG.

    :param answer: the child's answer
    :returns: True for ``{"png": str}``; the worker decodes and checks it
    """
    return answer.keys() == {"png"} and isinstance(answer["png"], str)


def _is_page(answer: dict, args: list[str]) -> bool:
    """Tell whether an answer is what ``page`` gives.

    :param answer: the child's answer
    :param args: ``page``, the number, the resolution and the most characters
    :returns: True for ``{"text": str}`` within the most characters, or a PNG
    """
    if answer.keys() == {"text"}:
        return _is_text(answer["text"], int(args[3]))
    return _is_png(answer)


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
    :returns: True for a text within the most characters, a confidence and a
        model's name
    """
    return (
        answer.keys() == {"text", "confidence", "model"}
        and _is_text(answer["text"], int(args[4]))
        and _is_confidence(answer["confidence"])
        and _is_text(answer["model"], MODEL_CHARS)
        and bool(answer["model"])
    )


_OPERATIONS: dict[
    str,
    tuple[Callable[[list[str], bytes], int], Callable[[dict, list[str]], bool]],
] = {
    "parts": (
        lambda args, data: parts_answer_bytes(int(args[2]), int(args[3])),
        _is_parts,
    ),
    "survey": (lambda args, data: ANSWER_SLACK, _is_survey),
    "page": (
        lambda args, data: max(
            text_answer_bytes(int(args[3])), png_answer_bytes(MAX_PIXELS)
        ),
        _is_page,
    ),
    "frame": (
        lambda args, data: png_answer_bytes(MAX_PIXELS),
        lambda answer, args: _is_png(answer),
    ),
    "ocr": (lambda args, data: text_answer_bytes(int(args[4])), _is_ocr),
}
"""Each operation's bound on its answer, derived from its arguments and the
limits, and the check of its answer's shape. An operation not here gets
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
    """Kill the child and every process it started.

    :param process: the child, leader of its own process group
    """
    # The group is gone already, or only zombies are left in it, which
    # macOS answers with a refusal.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


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
    (:func:`text_answer_bytes`).

    :param args: the operation and its operands, as
        :func:`reef.reading.child.main` takes them after the limits
    :param data: the bytes the child reads from stdin
    :param seconds: the longest the child may run, wall clock; its CPU time
        is capped at this, rounded up
    :param answer_bytes: the most the child may print; by default the
        operation's own bound, else :data:`DEFAULT_ANSWER_BYTES`
    :returns: the child's answer, a JSON object without an ``error`` key,
        in the operation's shape
    :raises ReadTimedOut: when the child runs out of time
    :raises ReadFailed: the class the child named, when it named one
    :raises ChildCrashed: when the child prints more than its bound, or
        ends without an answer of the right shape
    :raises SandboxUnavailable: when the child cannot be started safely
    """
    bound, shaped = _OPERATIONS.get(args[0], (None, None))
    if answer_bytes is None:
        answer_bytes = DEFAULT_ANSWER_BYTES if bound is None else bound(args, data)
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
    finished = False
    try:
        async with asyncio.timeout(seconds):
            stdout = await _exchange(process, data, answer_bytes)
        finished = True
    except TimeoutError:
        raise ReadTimedOut from None
    finally:
        if not finished:
            # Timed out, cancelled or talking too much. The child may be gone
            # already while a process it started holds the pipe open, so the
            # group goes, whatever the child's own state.
            _kill_group(process)
            await process.wait()
    answer = _answer(process.returncode, stdout)
    if shaped is not None and not shaped(answer, args):
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


def _answer(returncode: int | None, stdout: bytes | bytearray) -> dict:
    """Turn what the child left behind into its answer or a failure.

    Raised outside any ``except`` block, so no failure carries the parse
    error, and with it the child's output, as its context.

    :param returncode: how the child ended
    :param stdout: everything it printed
    :returns: its answer
    :raises ReadTimedOut: when it ran out of CPU time
    :raises ReadFailed: the class it named
    :raises ChildCrashed: for anything else that is not an answer
    """
    if returncode == -signal.SIGXCPU:
        raise ReadTimedOut
    if returncode != 0:
        raise ChildCrashed
    try:
        answer = json.loads(stdout)
    except (ValueError, RecursionError):
        answer = None
    if not isinstance(answer, dict):
        raise ChildCrashed
    if "error" in answer:
        raise _FAILURES.get(str(answer["error"]), ChildCrashed)
    return answer
