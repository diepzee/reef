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
- has its stderr thrown away, and is heard on stdout only up to
  :data:`ANSWER_BYTES`.

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

READER_USER = "reader"
"""The unprivileged user a root worker runs the child as. The Docker image
creates it."""

ANSWER_BYTES = 256 * 1024 * 1024
"""The most a child may print. A 25 MB file gives far less; a child that
prints more is not answering, and would cost the worker its memory."""

_CHUNK_BYTES = 64 * 1024

_FAILURES: dict[str, type[ReadFailed]] = {
    failure.__name__: failure
    for failure in (Unreadable, Encrypted, TooLarge, ReadTimedOut, ChildCrashed)
}
"""The failures a child may name. Any other name counts as a crash."""


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


async def run_child(args: list[str], data: bytes, *, seconds: float) -> dict:
    """Run one operation in the child and return its answer.

    :param args: the operation and its operands, as
        :func:`reef.reading.child.main` takes them after the limits
    :param data: the bytes the child reads from stdin
    :param seconds: the longest the child may run, wall clock; its CPU time
        is capped at this, rounded up
    :returns: the child's answer, a JSON object without an ``error`` key
    :raises ReadTimedOut: when the child runs out of time
    :raises ReadFailed: the class the child named, when it named one
    :raises ChildCrashed: when the child ends without a usable answer
    :raises SandboxUnavailable: when the child cannot be started safely
    """
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
            stdout = await _exchange(process, data)
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
    return _answer(process.returncode, stdout)


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


async def _exchange(process: asyncio.subprocess.Process, data: bytes) -> bytes:
    """Give the child the file and take its answer, as ``communicate`` would.

    Unlike ``communicate``, it stops listening at :data:`ANSWER_BYTES`.

    :param process: the child, started with pipes for stdin and stdout
    :param data: the file's bytes
    :returns: everything the child printed, once it has ended
    :raises ChildCrashed: when the child prints more than it may
    """
    assert process.stdin is not None and process.stdout is not None
    feeder = asyncio.create_task(_feed(process.stdin, data))
    try:
        chunks: list[bytes] = []
        size = 0
        while chunk := await process.stdout.read(_CHUNK_BYTES):
            size += len(chunk)
            if size > ANSWER_BYTES:
                raise ChildCrashed
            chunks.append(chunk)
        await feeder
        await process.wait()
    finally:
        feeder.cancel()
    return b"".join(chunks)


def _answer(returncode: int | None, stdout: bytes) -> dict:
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
