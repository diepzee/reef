"""The child process that parses one file: bytes in on stdin, JSON out on stdout.

Started by :func:`reef.reading.sandbox.run_child`, never by hand::

    python -I -B -m reef.reading.child --memory-mb N --cpu-seconds S <op> ...

It caps itself before it reads a byte, reads the whole file from stdin, runs
one operation and prints one JSON object. A read that fails prints
``{"error": "<class>"}``, the class of a :class:`~reef.reading.ReadFailed`,
and nothing else. Anything unexpected ends the child without an answer; its
traceback goes to stderr, which the worker throws away.

Operations:

``parts <kind> <part_chars> <max_parts>``
    Read a file into parts of at most ``part_chars`` characters:
    ``{"parts": [...], "pages": N}``. At most ``max_parts`` parts are given.
    ``pages`` is the true number of parts for a text format. For Word, Excel
    and PowerPoint extraction stops once there are more than ``max_parts``,
    so ``pages`` is then ``max_parts + 1``: "more than ``max_parts``".

This process reads no setting and no environment variable: it gets none.
"""

import argparse
import json
import resource
import sys
from collections.abc import Callable

from reef.reading import FileKind, ReadFailed, TooLarge
from reef.reading.formats import text_parts

_OPEN_FILES = 64
"""Enough for a parser and the programs it runs, not for much else."""


def _cap(limit: int, value: int, *, slack: int = 0) -> None:
    """Set a resource limit, never above its current hard limit.

    :param limit: the resource, such as ``resource.RLIMIT_CPU``
    :param value: the new soft limit
    :param slack: how far the hard limit sits above the soft one
    """
    _, hard = resource.getrlimit(limit)
    if hard != resource.RLIM_INFINITY:
        value = min(value, hard)
        top = min(value + slack, hard)
    else:
        top = value + slack
    resource.setrlimit(limit, (value, top))


def apply_limits(memory_mb: int, cpu_seconds: int) -> None:
    """Cap this process before it touches a file.

    Memory is capped as address space, on Linux only: macOS does not
    enforce it. CPU time ends the process with ``SIGXCPU``, and one second
    later with ``SIGKILL`` if that signal is ignored. No core dump is
    written: it would hold the file.

    :param memory_mb: the most address space, in MB
    :param cpu_seconds: the most CPU time, in seconds
    """
    if sys.platform.startswith("linux"):
        _cap(resource.RLIMIT_AS, memory_mb * 1024 * 1024)
    _cap(resource.RLIMIT_CPU, cpu_seconds, slack=1)
    _cap(resource.RLIMIT_NOFILE, _OPEN_FILES)
    _cap(resource.RLIMIT_CORE, 0)


def _parts(data: bytes, operands: list[str]) -> dict:
    """Read a text format into parts.

    :param data: the file's bytes
    :param operands: the kind, the most characters in one part and the most
        parts to give
    :returns: ``{"parts": [...], "pages": N}``
    """
    kind, part_chars, max_parts = operands
    parts = text_parts(data, FileKind(kind), int(part_chars), int(max_parts))
    return {"parts": parts[: int(max_parts)], "pages": len(parts)}


_OPERATIONS: dict[str, Callable[[bytes, list[str]], dict]] = {"parts": _parts}


def _arguments(argv: list[str]) -> argparse.Namespace:
    """Read the command line.

    :param argv: the arguments after the program name
    :returns: the limits, the operation and its operands
    """
    parser = argparse.ArgumentParser(prog="reef.reading.child", add_help=False)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("operation", choices=sorted(_OPERATIONS))
    parser.add_argument("operands", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Cap this process, read the file from stdin, answer on stdout.

    :param argv: ``--memory-mb N --cpu-seconds S <op> ...``
    :returns: 0 once an answer is printed, a failure's class included
    """
    arguments = _arguments(argv)
    apply_limits(arguments.memory_mb, arguments.cpu_seconds)
    try:
        data = sys.stdin.buffer.read()
        answer = _OPERATIONS[arguments.operation](data, arguments.operands)
    except ReadFailed as failure:
        answer = {"error": type(failure).__name__}
    except MemoryError:
        answer = {"error": TooLarge.__name__}
    output = json.dumps(answer, ensure_ascii=False).encode("utf-8", "replace")
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
