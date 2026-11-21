"""Every file is parsed in a child process that cannot reach the worker's secrets."""

import asyncio
import json
import os
import pwd
import subprocess
import sys
import time
from pathlib import Path

import pytest

from reef.config import get_settings
from reef.reading import (
    ChildCrashed,
    Encrypted,
    ReadFailed,
    ReadTimedOut,
    SandboxUnavailable,
    TooLarge,
    Unreadable,
    child,
    sandbox,
)

ON_LINUX = sys.platform.startswith("linux")

_real_spawn = asyncio.create_subprocess_exec


class Spawned:
    """Records what the sandbox asked of ``asyncio.create_subprocess_exec``."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.command: list[str] = []
        self.options: dict = {}
        self.process: asyncio.subprocess.Process | None = None

    async def __call__(self, *command: str, **options) -> asyncio.subprocess.Process:
        """Record the call and start the process for real.

        :param command: the program and its arguments
        :param options: the keyword arguments
        :returns: the started process
        """
        self.command = list(command)
        self.options = options
        self.process = await _real_spawn(*command, **options)
        return self.process


@pytest.fixture
def spawned(monkeypatch) -> Spawned:
    """Watch every child the sandbox starts.

    :param monkeypatch: pytest's patcher
    :returns: the recorder
    """
    recorder = Spawned()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    return recorder


def _child_runs(monkeypatch, code: str) -> None:
    """Make the sandbox start a small Python program instead of the reader.

    :param monkeypatch: pytest's patcher
    :param code: the program
    """
    monkeypatch.setattr(sandbox, "child_command", lambda: [sys.executable, "-c", code])


def _running(pid: int) -> bool:
    """Tell whether a process still runs: gone and zombie both count as stopped.

    :param pid: the process
    :returns: True while it runs
    """
    if Path("/proc/self/stat").exists():
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except FileNotFoundError:
            return False
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


async def test_the_child_gets_no_variable_of_the_worker(monkeypatch, spawned):
    """A storage key in the worker's environment does not reach the child."""
    monkeypatch.setenv("REEF_S3_SECRET_KEY", "canary")
    await sandbox.run_child(["parts", "plain", "20000", "200"], b"hello", seconds=30)
    assert spawned.options["env"] == sandbox.child_environment()
    assert sandbox.child_environment() == {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "OMP_THREAD_LIMIT": "1",
    }
    assert "canary" not in json.dumps(spawned.options["env"])


async def test_the_child_is_started_isolated_and_limited(spawned):
    """Isolated mode, no bytecode, a new session, stderr dropped, limits first.

    Not root here, so the child runs as the worker's own user and gets no
    process cap: it would count all of that user's processes.
    """
    seconds = 2.5
    await sandbox.run_child(
        ["parts", "plain", "20000", "200"], b"hello", seconds=seconds
    )
    memory = get_settings().reading.memory_mb
    assert spawned.command == [
        *sandbox.child_command(),
        "--memory-mb",
        str(memory),
        "--cpu-seconds",
        "3",
        "parts",
        "plain",
        "20000",
        "200",
    ]
    assert sandbox.child_command()[1:] == ["-I", "-B", "-m", "reef.reading.child"]
    assert spawned.options["start_new_session"] is True
    assert spawned.options["stderr"] == asyncio.subprocess.DEVNULL


def test_the_child_runs_as_reader_when_the_worker_is_root(monkeypatch):
    """Root hands the file to ``reader``; without ``reader`` there is no sandbox."""
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 1000)
    assert sandbox.child_user() is None

    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 0)
    reader = pwd.struct_passwd(
        ("reader", "x", 999, 999, "", "/nonexistent", "/usr/sbin/nologin")
    )
    monkeypatch.setattr(sandbox.pwd, "getpwnam", lambda name: reader)
    assert sandbox.child_user() == "reader"

    def missing(name: str) -> pwd.struct_passwd:
        """Stand in for a system without the user.

        :param name: the user asked for
        :raises KeyError: always
        """
        raise KeyError(name)

    monkeypatch.setattr(sandbox.pwd, "getpwnam", missing)
    with pytest.raises(SandboxUnavailable):
        sandbox.child_user()


async def test_a_root_worker_drops_its_groups_too(monkeypatch):
    """The child gets reader's user and group, and no supplementary groups."""
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 0)
    reader = pwd.struct_passwd(
        ("reader", "x", 999, 998, "", "/nonexistent", "/usr/sbin/nologin")
    )
    monkeypatch.setattr(sandbox.pwd, "getpwnam", lambda name: reader)
    seen: dict = {}

    class Stop(Exception):
        """Ends the call before anything starts."""

    async def spawn(*command: str, **options) -> None:
        """Record the options and start nothing.

        :param command: the program and its arguments
        :param options: the keyword arguments
        :raises Stop: always
        """
        seen.update(options)
        raise Stop

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(Stop):
        await sandbox.run_child(["parts", "plain", "20000", "200"], b"x", seconds=5)
    assert (seen["user"], seen["group"], seen["extra_groups"]) == (999, 998, [])


async def test_a_child_that_runs_as_reader_gets_a_process_cap(monkeypatch):
    """Enough for each page read at once and its Tesseract, not for a fork chain.

    The cap counts every process of ``reader``, so it grows with the pages
    read at the same time. A child that runs as the worker's own user gets
    none: see :func:`test_the_child_is_started_isolated_and_limited`.
    """
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 0)
    reader = pwd.struct_passwd(
        ("reader", "x", 999, 998, "", "/nonexistent", "/usr/sbin/nologin")
    )
    monkeypatch.setattr(sandbox.pwd, "getpwnam", lambda name: reader)
    monkeypatch.setattr(get_settings().reading, "parallel_pages", 3)
    seen: list[str] = []

    class Stop(Exception):
        """Ends the call before anything starts."""

    async def spawn(*command: str, **options) -> None:
        """Record the command and start nothing.

        :param command: the program and its arguments
        :param options: the keyword arguments
        :raises Stop: always
        """
        seen.extend(command)
        raise Stop

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(Stop):
        await sandbox.run_child(["parts", "plain", "20000", "200"], b"x", seconds=5)
    assert seen == [
        *sandbox.child_command(),
        "--memory-mb",
        str(get_settings().reading.memory_mb),
        "--cpu-seconds",
        "5",
        "--processes",
        str(sandbox.PROCESSES_PER_PAGE * 3),
        "parts",
        "plain",
        "20000",
        "200",
    ]


async def test_a_child_that_runs_too_long_is_stopped(monkeypatch, spawned):
    """Past its time the child is killed and the read has timed out."""
    _child_runs(monkeypatch, "import time; time.sleep(5)")
    started = time.monotonic()
    with pytest.raises(ReadTimedOut):
        await sandbox.run_child(["parts", "plain", "20000", "200"], b"x", seconds=0.5)
    assert time.monotonic() - started < 2
    assert spawned.process is not None
    assert spawned.process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(spawned.process.pid, 0)


async def test_a_stopped_child_takes_its_own_children_with_it(monkeypatch, tmp_path):
    """The whole process group goes, not only the child."""
    pid_file = tmp_path / "grandchild"
    code = (
        "import subprocess, sys, time\n"
        "grandchild = subprocess.Popen(['sleep', '30'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(grandchild.pid))\n"
        "time.sleep(30)\n"
    )
    _child_runs(monkeypatch, code)
    with pytest.raises(ReadTimedOut):
        await sandbox.run_child(["parts", "plain", "20000", "200"], b"x", seconds=1)
    grandchild = int(Path(pid_file).read_text())
    deadline = time.monotonic() + 2
    while _running(grandchild) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert not _running(grandchild)


@pytest.mark.parametrize(
    "code",
    [
        "import sys; print('secret'); sys.stderr.write('secret'); sys.exit(3)",
        "print('secret')",
        "print('[\"secret\"]')",
        'print(\'{"error": "secret"}\')',
        "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
        "print('[' * 100_000 + 'secret')",
        "import sys; sys.stdout.buffer.write(b'\\xffsecret')",
    ],
)
async def test_a_crashing_child_is_reported_by_class_only(monkeypatch, code: str):
    """Whatever the child says or how it dies, only the class comes back.

    :param code: what the child does
    """
    _child_runs(monkeypatch, code)
    with pytest.raises(ChildCrashed) as caught:
        # An operation of no fixed shape, so each answer reaches the parser.
        await sandbox.run_child(["x"], b"x", seconds=10)
    error = caught.value
    assert "secret" not in str(error)
    assert "secret" not in repr(error)
    assert error.args == ()
    assert error.__cause__ is None
    assert error.__context__ is None


async def test_a_child_out_of_cpu_time_has_timed_out(monkeypatch):
    """The CPU limit ends a read as surely as the clock does."""
    _child_runs(
        monkeypatch,
        "import os, resource, signal\n"
        "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
        "os.kill(os.getpid(), signal.SIGXCPU)\n",
    )
    with pytest.raises(ReadTimedOut):
        await sandbox.run_child(["parts", "plain", "20000", "200"], b"x", seconds=10)


@pytest.mark.parametrize(
    "failure", [Unreadable, Encrypted, TooLarge, ReadTimedOut, ChildCrashed]
)
async def test_a_childs_error_class_is_raised_again(monkeypatch, failure):
    """The child names a failure; the worker raises that class, bare.

    :param failure: the class the child names
    """
    _child_runs(monkeypatch, f'print(\'{{"error": "{failure.__name__}"}}\')')
    with pytest.raises(failure) as caught:
        await sandbox.run_child(["parts", "plain", "20000", "200"], b"x", seconds=10)
    assert type(caught.value) is failure
    assert issubclass(failure, ReadFailed)
    assert caught.value.args == ()


def test_a_missing_sandbox_is_not_a_failed_read():
    """The queue retries a job whose sandbox is missing; a failed read is final."""
    assert not issubclass(SandboxUnavailable, ReadFailed)


async def test_a_markdown_file_is_read_in_the_child():
    """The real child reads a Markdown file into parts."""
    data = "# Notes\n\nCafé at nine.".encode()
    answer = await sandbox.run_child(
        ["parts", "markdown", "20000", "200"], data, seconds=30
    )
    assert answer == {"parts": ["# Notes\n\nCafé at nine."], "pages": 1}


async def test_the_child_reads_html_and_cuts_long_text():
    """HTML loses its markup, and long text comes back in parts."""
    page = b"<p>one</p><script>ZEBRAQUILT</script><p>two</p>"
    answer = await sandbox.run_child(
        ["parts", "html", "20000", "200"], page, seconds=30
    )
    assert answer == {"parts": ["one\n\ntwo"], "pages": 1}
    long = "\n\n".join(["x" * 1_000] * 2_000).encode()
    answer = await sandbox.run_child(
        ["parts", "plain", "20000", "200"], long, seconds=30
    )
    assert len(answer["parts"]) == answer["pages"] == 106
    assert "\n\n".join(answer["parts"]).encode() == long


async def test_a_child_that_talks_too_much_is_stopped(monkeypatch, spawned):
    """Past the bound it was given, a child has crashed, and it is killed."""
    _child_runs(
        monkeypatch,
        "import sys, time\nsys.stdout.write('x' * 200_000)\nsys.stdout.flush()\n"
        "time.sleep(30)\n",
    )
    started = time.monotonic()
    with pytest.raises(ChildCrashed):
        await sandbox.run_child(["x"], b"x", seconds=20, answer_bytes=100_000)
    assert time.monotonic() - started < 5
    assert spawned.process is not None
    assert spawned.process.returncode is not None


async def test_a_parts_answer_is_bounded_by_its_file(monkeypatch):
    """A child reading parts may print what the limits allow, no more.

    The bound comes from the part size and the most parts, never from the
    file. The worker stops one byte past it, so what it holds, and what it
    would parse, is never more.
    """
    read = 0

    async def spawn(*command: str, **options) -> asyncio.subprocess.Process:
        """Start the child, counting what is read from its stdout.

        :param command: the program and its arguments
        :param options: the keyword arguments
        :returns: the started process
        """
        process = await _real_spawn(*command, **options)
        stdout = process.stdout
        assert stdout is not None

        class Counted:
            """The child's stdout, counting every byte taken from it."""

            async def read(self, n: int = -1) -> bytes:
                """Read and count.

                :param n: the most bytes to read
                :returns: what was read
                """
                nonlocal read
                chunk = await stdout.read(n)
                read += len(chunk)
                return chunk

        process.stdout = Counted()  # type: ignore[assignment]
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    _child_runs(
        monkeypatch,
        "import sys, time\nsys.stdout.write('x' * 20_000_000)\nsys.stdout.flush()\n"
        "time.sleep(30)\n",
    )
    data = b"x" * 10_000
    bound = sandbox.parts_answer_bytes(20_000, 200)
    assert bound < 17_000_000
    assert bound == 200 * (4 * 20_000 + 4) + sandbox.ANSWER_SLACK
    with pytest.raises(ChildCrashed):
        await sandbox.run_child(["parts", "plain", "20000", "200"], data, seconds=20)
    assert read == bound + 1


@pytest.mark.parametrize(
    "answer",
    [
        {"parts": "one"},
        {"parts": [1]},
        {"parts": [["one"]]},
        {"parts": None},
        {"parts": ["one"], "pages": 1, "more": True},
        {"parts": ["one"]},
        {"parts": ["one"], "pages": "1"},
        {"parts": ["one"], "pages": True},
        {"parts": ["one", "two"], "pages": 1},
        {"parts": ["x"] * 201, "pages": 201},
        {"pages": 1},
        {},
    ],
)
async def test_a_parts_answer_of_the_wrong_shape_is_a_crash(monkeypatch, answer):
    """Only a list of strings, at most the most parts, and a page count at
    least as large, is a ``parts`` answer.

    :param answer: what the child prints
    """
    _child_runs(monkeypatch, f"print({json.dumps(json.dumps(answer))})")
    with pytest.raises(ChildCrashed):
        await sandbox.run_child(["parts", "plain", "20000", "200"], b"x", seconds=10)


async def test_a_parts_answer_of_the_right_shape_is_returned(monkeypatch):
    """A list of strings, empty or not, is an answer."""
    for parts in ([], ["one", "two"]):
        _child_runs(
            monkeypatch,
            f"print({json.dumps(json.dumps({'parts': parts, 'pages': len(parts)}))})",
        )
        answer = await sandbox.run_child(
            ["parts", "plain", "20000", "200"], b"x", seconds=10
        )
        assert answer == {"parts": parts, "pages": len(parts)}


async def test_a_child_need_not_read_all_it_is_given(monkeypatch):
    """A child that answers before reading the whole file still answers."""
    _child_runs(monkeypatch, "print('{\"parts\": []}')")
    answer = await sandbox.run_child(["x"], b"x" * 5_000_000, seconds=10)
    assert answer == {"parts": []}


class _Stdin:
    """Stands in for ``sys.stdin``, noting when the file is read."""

    def __init__(self, events: list[str], data: bytes | None) -> None:
        """Hold the file, or None to fail for lack of memory.

        :param events: where to note the read
        :param data: what the read returns
        """
        self.buffer = self
        self._events = events
        self._data = data

    def read(self) -> bytes:
        """Note the read and return the file.

        :returns: the file
        :raises MemoryError: when there is no file to give
        """
        self._events.append("read")
        if self._data is None:
            raise MemoryError
        return self._data


ARGV = ["--memory-mb", "512", "--cpu-seconds", "7", "parts", "markdown", "20000", "200"]


@pytest.mark.parametrize(
    ("argv", "processes"),
    [(ARGV, "None"), (["--processes", "16", *ARGV], "16")],
)
def test_the_child_caps_itself_before_it_reads_the_file(
    monkeypatch, capsysbinary, argv: list[str], processes: str
):
    """Not one byte of the file is read before the limits are in place.

    :param argv: the child's arguments, with or without a process cap
    :param processes: the cap the limits are given
    """
    events: list[str] = []
    monkeypatch.setattr(
        child,
        "apply_limits",
        lambda memory, cpu, processes: events.append(
            f"limits {memory} {cpu} {processes}"
        ),
    )
    monkeypatch.setattr(child.sys, "stdin", _Stdin(events, b"# Notes"))
    assert child.main(argv) == 0
    assert events == [f"limits 512 7 {processes}", "read"]
    assert json.loads(capsysbinary.readouterr().out) == {
        "parts": ["# Notes"],
        "pages": 1,
    }


def test_running_out_of_memory_while_reading_is_too_large(monkeypatch, capsysbinary):
    """A file too large to read in is ``TooLarge``, not a crash."""
    monkeypatch.setattr(child, "apply_limits", lambda memory, cpu, processes: None)
    monkeypatch.setattr(child.sys, "stdin", _Stdin([], None))
    assert child.main(ARGV) == 0
    assert json.loads(capsysbinary.readouterr().out) == {"error": "TooLarge"}


@pytest.mark.skipif(not ON_LINUX, reason="RLIMIT_AS is set on Linux only")
def test_the_child_limits_itself():
    """The child's address space is capped at what it was given."""
    code = (
        "import resource\n"
        "from reef.reading.child import apply_limits\n"
        "apply_limits(64, 5)\n"
        "print(resource.getrlimit(resource.RLIMIT_AS)[0])\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    assert int(out.stdout) == 64 * 1024 * 1024


def test_the_child_caps_cpu_files_and_core_dumps():
    """CPU time, open files and core dumps are capped on every platform."""
    code = (
        "import json, resource\n"
        "from reef.reading.child import apply_limits\n"
        "apply_limits(64, 5)\n"
        "print(json.dumps([resource.getrlimit(r) for r in (\n"
        "    resource.RLIMIT_CPU, resource.RLIMIT_NOFILE, resource.RLIMIT_CORE)]))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    cpu, files, core = json.loads(out.stdout)
    assert cpu[0] == 5
    assert files == [64, 64]
    assert core == [0, 0]


@pytest.mark.skipif(not ON_LINUX, reason="RLIMIT_NPROC is set on Linux only")
def test_the_child_caps_its_processes():
    """A fork chain stops at the cap: the soft and the hard limit are both set."""
    code = (
        "import json, resource\n"
        "from reef.reading.child import apply_limits\n"
        "apply_limits(64, 5, 16)\n"
        "print(json.dumps(resource.getrlimit(resource.RLIMIT_NPROC)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) == [16, 16]


@pytest.mark.parametrize(
    ("platform", "processes", "capped"),
    [("linux", 16, True), ("linux", None, False), ("darwin", 16, False)],
)
def test_the_process_cap_is_set_on_linux_when_given(
    monkeypatch, platform: str, processes: int | None, capped: bool
):
    """Linux counts the processes of the child's real user, across the system.

    So the sandbox gives a cap only to a child that runs as ``reader``, and
    only Linux sets it.

    :param platform: the platform the child runs on
    :param processes: the cap the child is given, if any
    :param capped: whether ``RLIMIT_NPROC`` is set
    """
    limits: list[int] = []
    monkeypatch.setattr(child.sys, "platform", platform)
    monkeypatch.setattr(
        child.resource,
        "getrlimit",
        lambda limit: (child.resource.RLIM_INFINITY, child.resource.RLIM_INFINITY),
    )
    monkeypatch.setattr(
        child.resource, "setrlimit", lambda limit, values: limits.append(limit)
    )
    child.apply_limits(64, 5, processes)
    assert (child.resource.RLIMIT_NPROC in limits) is capped


_SURVEY = ["survey", "pdf", "200"]
_SURVEY_IMAGE = ["survey", "image", "200"]
_PAGE = ["page", "1", "200", "1000"]
_FRAME = ["frame", "1"]
_OCR = ["ocr", "/usr/bin/tesseract", "nld+fra+eng", "200", "1000"]
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
"""Enough of a PNG for the worker, which never decodes one."""


def _child_writes(monkeypatch, output: bytes) -> None:
    """Make the sandbox start a child that writes these bytes and ends.

    :param monkeypatch: pytest's patcher
    :param output: everything the child prints
    """
    _child_runs(monkeypatch, f"import sys; sys.stdout.buffer.write({output!r})")


def _with_png(header: dict, png: bytes = _PNG) -> bytes:
    """Frame a PNG as the child does: a JSON line, then the raw bytes.

    :param header: the JSON, its ``png`` the PNG's length unless given
    :param png: the bytes that follow
    :returns: the output
    """
    return json.dumps({"png": len(png), **header}).encode() + b"\n" + png


@pytest.mark.parametrize(
    ("args", "output"),
    [
        (_SURVEY, b'{"pages": 0}'),
        (_SURVEY, b'{"pages": true}'),
        (_SURVEY, b'{"pages": "1"}'),
        (_SURVEY, b'{"pages": 1.0}'),
        (_SURVEY, b'{"pages": 1, "more": 1}'),
        (_SURVEY, b"{}"),
        (_SURVEY, b'{"pages": 1}\nmore'),
        (_SURVEY_IMAGE, b'{"pages": 202}'),
        (_PAGE, json.dumps({"text": "x" * 1_001}).encode()),
        (_PAGE, b'{"text": 1}'),
        (_PAGE, b'{"text": "a"}\n\x89PNG'),
        (_PAGE, _with_png({})),
        (_PAGE, _with_png({"dpi": 99})),
        (_PAGE, _with_png({"dpi": 201})),
        (_PAGE, _with_png({"dpi": True})),
        (_PAGE, _with_png({"dpi": 200.0})),
        (_PAGE, _with_png({"dpi": 200, "text": "a"})),
        (_PAGE, _with_png({"dpi": 200}, b"GIF89a" + b"\x00" * 26)),
        (_PAGE, _with_png({"dpi": 200, "png": len(_PNG) + 1})),
        (_PAGE, _with_png({"dpi": 200, "png": len(_PNG) - 1})),
        (_PAGE, _with_png({"dpi": 200, "png": True})),
        (_PAGE, _with_png({"dpi": 200, "png": "32"})),
        (_PAGE, b'{"png": 0, "dpi": 200}'),
        (_PAGE, b'{"png": "iVBORw0KGgo=", "dpi": 200}'),
        (_FRAME, _with_png({"dpi": 200})),
        (_FRAME, _with_png({}, b"")),
        (_FRAME, _with_png({"png": -1})),
        (_FRAME, b'{"text": "a"}'),
        (_OCR, b'{"text": "a"}'),
        (_OCR, json.dumps({"text": "x" * 1_001, "confidence": 0.9}).encode()),
        (_OCR, b'{"text": "a", "confidence": 1.5}'),
        (_OCR, b'{"text": "a", "confidence": -0.1}'),
        (_OCR, b'{"text": "a", "confidence": true}'),
        (_OCR, b'{"text": "a", "confidence": "0.9"}'),
        (_OCR, b'{"text": "a", "confidence": NaN}'),
        (_OCR, b'{"text": "a", "confidence": 0.9, "model": "t"}'),
    ],
)
async def test_a_page_answer_of_the_wrong_shape_is_a_crash(monkeypatch, args, output):
    """Each page operation has one shape of answer; anything else is a crash.

    A PNG must be exactly as long as its header says, with nothing after it,
    and start like a PNG.

    :param args: the operation and its operands
    :param output: what the child prints
    """
    _child_writes(monkeypatch, output)
    with pytest.raises(ChildCrashed):
        await sandbox.run_child(args, b"x", seconds=10)


@pytest.mark.parametrize(
    ("args", "output", "answer"),
    [
        (_SURVEY, b'{"pages": 3}', {"pages": 3}),
        (_SURVEY, b'{"pages": 500}', {"pages": 500}),
        (_SURVEY_IMAGE, b'{"pages": 201}', {"pages": 201}),
        (_PAGE, b'{"text": "abc"}', {"text": "abc"}),
        (_PAGE, _with_png({"dpi": 200}), {"png": _PNG, "dpi": 200}),
        (_PAGE, _with_png({"dpi": 100}), {"png": _PNG, "dpi": 100}),
        (_FRAME, _with_png({}), {"png": _PNG}),
        (_FRAME, _with_png({}, _PNG + b"\n\n"), {"png": _PNG + b"\n\n"}),
        (_OCR, b'{"text": "", "confidence": null}', {"text": "", "confidence": None}),
        (_OCR, b'{"text": "a", "confidence": 1}', {"text": "a", "confidence": 1}),
    ],
)
async def test_a_page_answer_of_the_right_shape_is_returned(
    monkeypatch, args, output, answer
):
    """An answer in its operation's shape comes back; a PNG as its raw bytes.

    :param args: the operation and its operands
    :param output: what the child prints
    :param answer: what the worker gets
    """
    _child_writes(monkeypatch, output)
    assert await sandbox.run_child(args, b"x", seconds=10) == answer


@pytest.mark.parametrize(
    ("args", "bound"),
    [
        (_SURVEY, sandbox.ANSWER_SLACK),
        (_OCR, sandbox.text_answer_bytes(1_000)),
        (_FRAME, sandbox.png_answer_bytes(40_000_000)),
    ],
)
async def test_a_page_answer_is_bounded_by_the_limits(monkeypatch, args, bound):
    """A survey says one number, a reading one page of text, a frame one PNG.

    :param args: the operation and its operands
    :param bound: the most it may print
    """
    _child_runs(monkeypatch, f"import sys; sys.stdout.write('x' * {bound + 1})")
    with pytest.raises(ChildCrashed):
        await sandbox.run_child(args, b"x" * 50_000_000, seconds=20)
    assert sandbox.text_answer_bytes(1_000) == 4 * 1_000 + 4 + sandbox.ANSWER_SLACK


def test_the_png_bound_holds_for_the_worst_image():
    """A page 64 pixels wide, of pure noise, still fits its bound.

    The bound follows from the pixel cap alone, about 41 MB: no file can make
    the worker hold more.
    """
    from PIL import Image

    from reef.reading import pages

    for size in [(64, 31_250), (2_000, 1_000)]:
        pixels = size[0] * size[1]
        image = Image.frombytes("L", size, os.urandom(pixels))
        png = pages._png(image)
        output = json.dumps({"png": len(png), "dpi": 200}).encode() + b"\n" + png
        assert len(output) <= sandbox.png_answer_bytes(pixels)
        assert len(output) > pixels
    assert sandbox.png_answer_bytes(pages.MAX_PIXELS) < 41_000_000


# --- what the worker parses -----------------------------------------------------


def _padded(header: dict, chars: int) -> dict:
    """Make an answer's JSON longer than it needs to be.

    :param header: the answer
    :param chars: how many characters of padding to add
    :returns: the answer with a ``pad`` key
    """
    return {**header, "pad": "x" * chars}


@pytest.mark.parametrize(
    ("args", "output"),
    [
        # Text past one page's bound, though within the bound for a PNG.
        (_PAGE, json.dumps({"text": "x" * 70_000}).encode()),
        # A header line far longer than any header, before a PNG.
        (_PAGE, _with_png(_padded({"dpi": 200}, 70_000))),
        (_FRAME, _with_png(_padded({}, 70_000))),
        # A frame answers with a PNG; JSON alone can only be short.
        (_FRAME, json.dumps(_padded({"error": "Unreadable"}, 70_000)).encode()),
    ],
    ids=["page-text", "page-header", "frame-header", "frame-json"],
)
async def test_json_past_its_bound_is_not_parsed(monkeypatch, args, output):
    """The worker parses JSON only up to what an answer of that kind can be.

    Parsing takes many times its input in memory, so the JSON is bounded on
    its own, not only by everything the child may print.

    :param args: the operation and its operands
    :param output: what the child prints
    """
    parsed: list[int] = []
    loads = json.loads

    def counting(text, *more, **options):
        """Note how much is parsed, and parse it.

        :param text: the JSON
        :returns: what it holds
        """
        parsed.append(len(text))
        return loads(text, *more, **options)

    _child_writes(monkeypatch, output)
    monkeypatch.setattr(sandbox.json, "loads", counting)
    with pytest.raises(ChildCrashed):
        await sandbox.run_child(args, b"x", seconds=10)
    assert parsed == []


@pytest.mark.parametrize(
    ("args", "pages", "fits"),
    [
        # Word, Excel and PowerPoint stop counting at one past the limit.
        (["parts", "docx", "20000", "200"], 201, True),
        (["parts", "docx", "20000", "200"], 202, False),
        (["parts", "pptx", "20000", "200"], 10**12, False),
        # A text format counts every part; the count must fit the column.
        (["parts", "plain", "20000", "200"], 5_000, True),
        (["parts", "plain", "20000", "200"], 2**31 - 1, True),
        (["parts", "plain", "20000", "200"], 2**31, False),
        # A PDF says how many pages it has; an image counts to one past.
        (_SURVEY, 2**31 - 1, True),
        (_SURVEY, 2**31, False),
        (_SURVEY_IMAGE, 10**12, False),
    ],
)
async def test_a_page_count_is_bounded(monkeypatch, args, pages, fits):
    """No child can make the worker store a page count it could not hold.

    :param args: the operation and its operands
    :param pages: the count the child gives
    :param fits: whether the answer is taken
    """
    answer = {"pages": pages} if args[0] == "survey" else {"parts": [], "pages": pages}
    _child_writes(monkeypatch, json.dumps(answer).encode())
    if fits:
        assert await sandbox.run_child(args, b"x", seconds=10) == answer
    else:
        with pytest.raises(ChildCrashed):
            await sandbox.run_child(args, b"x", seconds=10)


# --- what a child leaves behind -------------------------------------------------

_FORKS_AND_ANSWERS = """
import json, os, sys, time
pid = os.fork()
if pid == 0:
    {escape}
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    time.sleep(30)
    os._exit(0)
print(json.dumps({{"parts": [str(pid)], "pages": 1}}))
"""
"""A child that starts a process, lets go of its pipes and answers at once."""


async def test_a_child_that_answers_leaves_no_process_in_its_group(monkeypatch):
    """The group is killed after every read, not only one that went wrong."""
    _child_runs(monkeypatch, _FORKS_AND_ANSWERS.format(escape="pass"))
    answer = await sandbox.run_child(
        ["parts", "plain", "20000", "200"], b"x", seconds=10
    )
    left = int(answer["parts"][0])
    deadline = time.monotonic() + 2
    while _running(left) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert not _running(left)


def _proc(
    root: Path,
    pid: str,
    uids: tuple[int, ...],
    state: str = "S",
    threads: int | None = 1,
) -> None:
    """Write a process's status as Linux shows it under ``/proc``.

    :param root: the stand-in for ``/proc``
    :param pid: the process id
    :param uids: real, effective, saved and file system user ids
    :param state: its state letter
    :param threads: how many threads it has; None leaves the line out
    """
    (root / pid).mkdir()
    count = "" if threads is None else f"Threads:\t{threads}\n"
    (root / pid / "status").write_text(
        f"Name:\tsleep\nState:\t{state} (x)\nPid:\t{pid}\n"
        f"Uid:\t{chr(9).join(str(uid) for uid in uids)}\nGid:\t0\t0\t0\t0\n"
        f"{count}"
    )


@pytest.fixture
def proc(tmp_path, monkeypatch) -> tuple[Path, list[int]]:
    """Stand in for ``/proc`` and ``os.kill``, on any platform.

    Holds a process of ``reader`` (999), one of root, a zombie of
    ``reader``, a setuid program ``reader`` started, an entry that is not a
    process and a process that ended while the sweep looked.

    :returns: the stand-in directory, and the ids killed, in order
    """
    _proc(tmp_path, "101", (999, 999, 999, 999))
    _proc(tmp_path, "102", (0, 0, 0, 0))
    _proc(tmp_path, "103", (999, 999, 999, 999), state="Z")
    _proc(tmp_path, "104", (999, 0, 0, 0))
    (tmp_path / "self").mkdir()
    (tmp_path / "105").mkdir()
    killed: list[int] = []

    def kill(pid: int, signal_number: int) -> None:
        """Note the process, and that it was to be killed outright.

        :param pid: the process
        :param signal_number: the signal
        """
        assert signal_number == sandbox.signal.SIGKILL
        killed.append(pid)

    monkeypatch.setattr(sandbox.os, "kill", kill)
    return tmp_path, killed


def test_every_live_process_of_the_reader_is_killed(proc):
    """Not root's, not a zombie, but a setuid program reader started, yes."""
    root, killed = proc
    assert sandbox.kill_owned_by(999, root) == 2
    assert killed == [101, 104]


def test_a_zombie_whose_threads_run_on_is_killed(proc):
    """Hostile code can end its main thread alone and keep the others running.

    Linux then shows the process as a zombie, with more than one thread, and
    those threads still run as ``reader``. A signal to its id reaches them
    all. A zombie whose thread count cannot be read is killed too.
    """
    root, killed = proc
    _proc(root, "106", (999, 999, 999, 999), state="Z", threads=2)
    _proc(root, "107", (999, 999, 999, 999), state="Z", threads=None)
    assert sandbox.kill_owned_by(999, root) == 4
    assert killed == [101, 104, 106, 107]


def test_a_process_started_during_the_sweep_is_killed_too(proc, monkeypatch):
    """A process may fork while it is being looked for; the sweep looks again."""
    root, killed = proc

    def kill(pid: int, signal_number: int) -> None:
        """Note the process; the first one forks as it dies.

        :param pid: the process
        :param signal_number: the signal
        """
        killed.append(pid)
        if pid == 101:
            _proc(root, "106", (999, 999, 999, 999))

    monkeypatch.setattr(sandbox.os, "kill", kill)
    assert sandbox.kill_owned_by(999, root) == 3
    assert killed == [101, 104, 106]


def test_the_reader_is_swept_only_by_a_root_worker_on_linux(proc, monkeypatch):
    """Elsewhere the child runs as the worker itself, which must live on."""
    root, killed = proc
    monkeypatch.setattr(sandbox, "PROC", root)
    monkeypatch.setattr(
        sandbox.pwd,
        "getpwnam",
        lambda name: pwd.struct_passwd((name, "x", 999, 999, "", "/", "/bin/false")),
    )
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 0)
    assert sandbox.kill_reader_processes() == 0
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 1000)
    assert sandbox.kill_reader_processes() == 0
    assert killed == []
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 0)
    assert sandbox.kill_reader_processes() == 2
    assert killed == [101, 104]


def test_a_root_worker_without_the_reader_sweeps_nothing(proc, monkeypatch):
    """No such user: no uid to look for, and nothing is killed."""
    root, killed = proc
    monkeypatch.setattr(sandbox, "PROC", root)
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 0)

    def missing(name: str):
        """Know no user.

        :param name: the user asked for
        :raises KeyError: always
        """
        raise KeyError(name)

    monkeypatch.setattr(sandbox.pwd, "getpwnam", missing)
    assert sandbox.kill_reader_processes() == 0
    assert killed == []


async def test_a_long_answer_may_end_with_a_line_break(monkeypatch):
    """JSON alone is bounded as JSON alone, whatever ends it."""
    parts = ["x" * 20_000] * 5
    _child_writes(
        monkeypatch, json.dumps({"parts": parts, "pages": 5}).encode() + b"\n"
    )
    answer = await sandbox.run_child(
        ["parts", "plain", "20000", "200"], b"x", seconds=10
    )
    assert answer == {"parts": parts, "pages": 5}
