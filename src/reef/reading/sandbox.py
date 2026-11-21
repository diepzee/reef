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
  derived from the file it was given (see :func:`run_child`).

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
    FileKind,
    ReadFailed,
    ReadTimedOut,
    SandboxUnavailable,
    TooLarge,
    Unreadable,
)
from reef.reading.archive import ZIP_MAX_ENTRIES, unpacked_size

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


def parts_answer_bytes(data: bytes, part_chars: int) -> int:
    """Return the most the ``parts`` operation can print for a file.

    Decoding at most triples a byte: Windows-1252 and the replacement
    character take up to three bytes in UTF-8. JSON escaping at most doubles
    a character that stays one byte, such as a quote. A tag turned into a
    line break stays within that too. Each part adds a quote pair and a
    separator; most parts also drop a blank line of the file that paid for
    them, and the others are at most one per ``part_chars`` characters.

    :param data: the file's bytes
    :param part_chars: the most characters in one part
    :returns: the bound, in bytes
    """
    return 3 * len(data) + 4 * (len(data) // part_chars + 1) + ANSWER_SLACK


def office_answer_bytes(data: bytes, part_chars: int) -> int:
    """Return the most the ``parts`` operation can print for an Office file.

    An Office file's text can be far larger than its compressed bytes, so
    :func:`parts_answer_bytes` is too small for it. The text cannot be more
    than the archive unpacks to: the child counts every character it reads
    against the unpacked size its directory declares, and gives up past it
    (:class:`~reef.reading.formats._Budget`). One character is at most four
    bytes in UTF-8 and JSON escaping at most doubles one that stays a byte,
    so the bound is four times that size, plus a quote pair and a separator
    for each part: a page per entry at most, and one more per
    ``part_chars`` characters.

    The directory is read here with :mod:`struct` only, never with a
    library: see :mod:`reef.reading.archive`.

    :param data: the file's bytes
    :param part_chars: the most characters in one part
    :returns: the bound, in bytes. When the directory is unreadable or too
        large the child will refuse the file; the bound is then the general
        one.
    """
    try:
        unpacked = unpacked_size(data)
    except ReadFailed:
        return parts_answer_bytes(data, part_chars)
    parts = ZIP_MAX_ENTRIES + unpacked // part_chars + 1
    return 4 * unpacked + 4 * parts + ANSWER_SLACK


_OFFICE = frozenset({FileKind.DOCX, FileKind.XLSX, FileKind.PPTX})


def _parts_bound(args: list[str], data: bytes) -> int:
    """Pick the bound for a ``parts`` answer by the file's kind.

    :param args: ``parts``, the kind and the most characters in one part
    :param data: the file's bytes
    :returns: the bound, in bytes
    """
    part_chars = int(args[2])
    if args[1] in _OFFICE:
        return office_answer_bytes(data, part_chars)
    return parts_answer_bytes(data, part_chars)


def _is_parts(answer: dict) -> bool:
    """Tell whether an answer is what ``parts`` gives: a list of strings.

    :param answer: the child's answer
    :returns: True for ``{"parts": [str, ...]}`` and nothing else
    """
    parts = answer.get("parts")
    return (
        answer.keys() == {"parts"}
        and isinstance(parts, list)
        and all(isinstance(part, str) for part in parts)
    )


_OPERATIONS: dict[
    str, tuple[Callable[[list[str], bytes], int], Callable[[dict], bool]]
] = {
    "parts": (_parts_bound, _is_parts),
}
"""Each operation's bound on its answer, derived from its arguments and
input, and the check of its answer's shape. An operation not here gets
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
    memory. Each operation needs a bound derived from its input, declared in
    ``_OPERATIONS`` or passed as ``answer_bytes``. ``parts`` declares
    :func:`parts_answer_bytes`, or :func:`office_answer_bytes` for Word,
    Excel and PowerPoint, and its answer's shape.

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
    if shaped is not None and not shaped(answer):
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
