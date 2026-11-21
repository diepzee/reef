"""Prove, inside the built image, that files are read in the sandbox.

Run as the image's own user, root, as the worker runs::

    docker run --rm reef:ci uv run --frozen --no-dev python \
        scripts/check_reading_in_image.py

It checks what a laptop cannot: that the image has a ``reader`` user, that a
root worker hands each file to it, that the child gets none of the worker's
variables and cannot read the worker's environment, and that Tesseract reads
Dutch, French and English there. It reads a Markdown file and a scan
through :mod:`reef.reading`, as the worker will, and as many scans at once
as the worker reads pages, under the child's cap on processes. Like the
worker, it adopts its children's orphans first.

It checks that the cap comes back: after a burst of reads that time out,
each leaving a Tesseract without a parent, and after a fork chain that
fills the cap, Tesseract and a Markdown read still work, with no sweep in
between, as within one job. It checks that no thread of ``reader``
outlives a read: neither one a normal read leaves, nor one a hostile child
starts, in its group or out of it, nor one whose process shows as a zombie
because its main thread alone has ended. Last, it checks that a sweep
leaves no zombie of ``reader`` at all, whatever its parent: one that went
to PID 1 would never be collected. The files are made here; nothing is
fetched. It prints counts and names only, and exits non-zero on the first
thing that is wrong.
"""

import asyncio
import io
import os
import pwd
import sys
import time
from pathlib import Path

from reef.config import get_settings
from reef.reading import (
    FileKind,
    ReadFailed,
    ReadTimedOut,
    SandboxUnavailable,
    ScanPage,
    local,
    sandbox,
)

LINES = (
    "Verzekering van de woning en de inboedel",
    "Assurance habitation et contenu du logement",
    "Insurance policy for the house and contents",
)
"""One Belgian page, in three languages."""

CANARY = "REEF_IMAGE_CHECK_CANARY"
"""A variable set in the worker that must not reach the child."""

PROBE = """
import json, os, pwd
try:
    with open(f"/proc/{os.getppid()}/environ", "rb") as environ:
        environ.read()
    readable = "yes"
except OSError:
    readable = "no"
names = ",".join(sorted(os.environ))
user = pwd.getpwuid(os.geteuid()).pw_name
print(json.dumps({"parts": [user, readable, names], "pages": 3}))
"""
"""Runs in the sandbox in place of the reader: says who it is, whether it can
read its parent's environment, and which variables it got."""

FORKS = """
import ctypes, json, os, threading, time
pid = os.fork()
if pid == 0:
    if {leave}:
        os.setsid()
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    if {hide}:
        threading.Thread(target=time.sleep, args=(120,)).start()
        ctypes.CDLL(None).syscall({exit_call}, 0)
    time.sleep(120)
    os._exit(0)
print(json.dumps({{"parts": [str(pid)], "pages": 1}}))
"""
"""Runs in the sandbox in place of the reader, as hostile code would: starts
a process that lets go of the pipes and sleeps, in the child's process group
or out of it, and answers at once. To hide, the process starts a thread
that sleeps and ends its main thread alone, with the raw ``exit`` system
call: Linux then shows it as a zombie while the thread runs on."""

EXIT_CALL = {"x86_64": 60, "aarch64": 93}
"""The number of the ``exit`` system call, which ends one thread, by machine."""

FORK_CHAIN = """
import json, os, sys, time
from reef.reading.child import _arguments, apply_limits
arguments = _arguments(sys.argv[1:])
apply_limits(arguments.memory_mb, arguments.cpu_seconds, arguments.processes)
started = 0
for _ in range(100):
    try:
        pid = os.fork()
    except BlockingIOError:
        break
    if pid == 0:
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)
        time.sleep(120)
        os._exit(0)
    started += 1
print(json.dumps({"parts": [str(started)], "pages": 1}))
"""
"""Runs in the sandbox in place of the reader: caps itself as the child
does, from the same arguments, then forks until Linux refuses, at most 100
times, and answers how many processes it started."""


def _scan() -> bytes:
    """Draw a scanned page of text, as a phone or scanner would give it.

    :returns: a PNG
    """
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", (1654, 900), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=48)
    for number, line in enumerate(LINES):
        draw.text((120, 150 + 90 * number), line, fill=0, font=font)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


async def _probe() -> list[str]:
    """Start the probe through the sandbox, as a file's reader is started.

    :returns: the child's user, whether it read the worker's environment,
        and the names of its variables
    """
    real = sandbox.child_command
    sandbox.child_command = lambda: [sys.executable, "-I", "-B", "-c", PROBE]
    try:
        answer = await sandbox.run_child(
            ["parts", "plain", "1000", "10"], b"", seconds=30
        )
    finally:
        sandbox.child_command = real
    return answer["parts"]


async def _in_child(code: str) -> str:
    """Run code through the sandbox, as a file's reader is started.

    :param code: what the child runs; it answers as ``parts`` does
    :returns: the first part of its answer
    """
    real = sandbox.child_command
    sandbox.child_command = lambda: [sys.executable, "-I", "-B", "-c", code]
    try:
        answer = await sandbox.run_child(
            ["parts", "plain", "1000", "10"], b"", seconds=30
        )
    finally:
        sandbox.child_command = real
    return answer["parts"][0]


async def _forks(leave: bool, hide: bool = False) -> int:
    """Start the forking child through the sandbox.

    :param leave: whether the process it starts leaves the child's group
    :param hide: whether that process ends its main thread alone
    :returns: the id of the process it started
    """
    exit_call = EXIT_CALL.get(os.uname().machine, 0)
    code = FORKS.format(leave=leave, hide=hide, exit_call=exit_call)
    return int(await _in_child(code))


def _state(pid: int) -> str:
    """Tell a process's state, as Linux shows it.

    :param pid: the process
    :returns: its state letter, or an empty string once it is gone
    """
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return ""
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    return fields.get("State", "").strip()[:1]


def _reader_threads() -> list[int]:
    """List the live threads of ``reader``, read from ``/proc`` here.

    Every thread, not every process: a process whose main thread has ended
    shows as a zombie while its other threads run. Not with
    :mod:`reef.reading.sandbox`, which is what is checked.

    :returns: their ids
    """
    uid = str(pwd.getpwnam(sandbox.READER_USER).pw_uid)
    found = []
    for status in Path("/proc").glob("[0-9]*/task/[0-9]*/status"):
        try:
            lines = status.read_text().splitlines()
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in lines if ":" in line)
        alive = fields.get("State", "").strip()[:1] not in ("Z", "X")
        if alive and fields.get("Uid", "").split()[:1] == [uid]:
            found.append(int(status.parent.name))
    return found


def _reader_zombies() -> int:
    """Count the zombies of ``reader``, whatever their parent.

    :returns: how many there are
    """
    uid = str(pwd.getpwnam(sandbox.READER_USER).pw_uid)
    found = 0
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            lines = status.read_text().splitlines()
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in lines if ":" in line)
        zombie = fields.get("State", "").strip()[:1] == "Z"
        found += zombie and fields.get("Uid", "").split()[:1] == [uid]
    return found


async def _still_reads(page: ScanPage, after: str) -> None:
    """Check that Tesseract and a Markdown read still work.

    :param page: a scan page Tesseract can read
    :param after: what came before, for the report
    """
    try:
        reading = await local.TesseractReader().read(page)
        outcome = "reads" if LINES[0].split()[0] in reading.text else "misreads"
    except (ReadFailed, SandboxUnavailable) as error:
        outcome = type(error).__name__
    _check(outcome == "reads", f"after {after}, Tesseract still {outcome} a page")
    try:
        parts, _ = await local.read_parts(b"# Notes", FileKind.MARKDOWN)
        outcome = "reads" if parts == ["# Notes"] else "misreads"
    except (ReadFailed, SandboxUnavailable) as error:
        outcome = type(error).__name__
    _check(outcome == "reads", f"and the child still {outcome} a Markdown file")


async def _time_out(page: ScanPage, wanted: int) -> tuple[int, str | None]:
    """Read a scan with too little time, until enough reads have timed out.

    Each read gets 70% of what one took, so it is killed while Tesseract
    runs, and Tesseract loses its parent.

    :param page: the scan page
    :param wanted: how many reads should time out
    :returns: how many did, out of at most three times as many tries; and
        the class of any other failure, which ends the burst
    """
    settings = get_settings().reading
    given = settings.page_seconds
    started = time.monotonic()
    await local.TesseractReader().read(page)
    settings.page_seconds = (time.monotonic() - started) * 0.7
    timed_out = 0
    try:
        for _ in range(wanted * 3):
            try:
                await local.TesseractReader().read(page)
            except ReadTimedOut:
                timed_out += 1
            except (ReadFailed, SandboxUnavailable) as error:
                return timed_out, type(error).__name__
            if timed_out == wanted:
                break
    finally:
        settings.page_seconds = given
    return timed_out, None


def _settled(count: int) -> int:
    """Wait up to two seconds for ``reader`` to have this many live threads.

    A killed process takes a moment to go.

    :param count: how many are expected
    :returns: how many there are
    """
    deadline = time.monotonic() + 2
    while len(_reader_threads()) != count and time.monotonic() < deadline:
        time.sleep(0.05)
    return len(_reader_threads())


def _check(ok: bool, what: str) -> None:
    """Stop at the first thing that is wrong.

    :param ok: whether it holds
    :param what: what should hold
    """
    print(f"{'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        sys.exit(1)


async def main() -> None:
    """Run every check, in order."""
    _check(os.geteuid() == 0, "the worker runs as root")
    _check(sandbox.child_user() == "reader", "a root worker reads as reader")
    _check(sandbox.adopt_orphans(), "it adopts its children's orphans, as the worker")

    os.environ[CANARY] = "canary"
    user, readable, names = await _probe()
    _check(user == "reader", f"the child runs as {user}")
    _check(readable == "no", "the child cannot read the worker's environment")
    _check(CANARY not in names, f"the child's variables are {names}")

    parts, pages = await local.read_parts(
        "# Notes\n\nCafé at nine.".encode(), FileKind.MARKDOWN
    )
    _check(
        (parts, pages) == (["# Notes\n\nCafé at nine."], 1),
        "the child reads a Markdown file",
    )

    page = await local.open_page(_scan(), FileKind.IMAGE, 1, dpi=200)
    _check(isinstance(page, ScanPage), "the child turns an image into a page")
    assert isinstance(page, ScanPage)
    reading = await local.TesseractReader().read(page)
    found = [line.split()[0] for line in LINES if line.split()[0] in reading.text]
    _check(len(found) == len(LINES), f"Tesseract read {', '.join(found)}")
    confidence = reading.confidence or 0
    _check(confidence > 0.6, f"with a mean confidence of {confidence:.2f}")
    print(f"     model: {reading.model}")

    parallel = get_settings().reading.parallel_pages
    cap = sandbox.PROCESSES_PER_PAGE * parallel
    readings = await asyncio.gather(
        *(local.TesseractReader().read(page) for _ in range(parallel))
    )
    read = sum(LINES[0].split()[0] in each.text for each in readings)
    _check(
        read == parallel, f"Tesseract reads {read} pages at once under a cap of {cap}"
    )

    _check(_settled(0) == 0, "no thread of reader outlives a read")

    timed_out, failed = await _time_out(page, cap + 4)
    burst = "short OCR reads only time out" + (f", not {failed}" if failed else "")
    _check(failed is None, burst)
    _check(timed_out > cap, f"{timed_out} OCR reads time out, more than the cap")
    _check(_settled(0) == 0, "and no thread of reader outlives them")
    await _still_reads(page, "they timed out")

    started = int(await _in_child(FORK_CHAIN))
    _check(0 < started < cap, f"a fork chain in the child is refused after {started}")
    _check(_settled(0) == 0, "and they die with the child")
    await _still_reads(page, "the fork chain")

    await _forks(leave=False)
    _check(_settled(0) == 0, "a process left in the child's group dies with it")
    await _forks(leave=True)
    _check(_settled(1) == 1, "a process that left the group outlives its read")
    killed = sandbox.kill_reader_processes()
    _check(killed == 1, f"the sweep after a reading job kills {killed}")
    _check(_settled(0) == 0, "and then no thread of reader is left")

    machine = os.uname().machine
    _check(machine in EXIT_CALL, f"a thread can end alone on {machine}")
    hidden = await _forks(leave=True, hide=True)
    _check(_settled(1) == 1, "a thread outlives its read once its main thread ends")
    _check(_state(hidden) == "Z", "while its process shows as a zombie")
    killed = sandbox.kill_reader_processes()
    _check(killed == 1, f"the sweep kills that process too: {killed}")
    _check(_settled(0) == 0, "and then no thread of reader is left")

    sandbox.kill_reader_processes()
    zombies = _reader_zombies()
    _check(zombies == 0, f"a sweep between jobs leaves {zombies} zombies of reader")


if __name__ == "__main__":
    asyncio.run(main())
