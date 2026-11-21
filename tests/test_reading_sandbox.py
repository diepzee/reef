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
    """Isolated mode, no bytecode, a new session, stderr dropped, limits first."""
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


def test_the_child_caps_itself_before_it_reads_the_file(monkeypatch, capsysbinary):
    """Not one byte of the file is read before the limits are in place."""
    events: list[str] = []
    monkeypatch.setattr(
        child,
        "apply_limits",
        lambda memory, cpu: events.append(f"limits {memory} {cpu}"),
    )
    monkeypatch.setattr(child.sys, "stdin", _Stdin(events, b"# Notes"))
    assert child.main(ARGV) == 0
    assert events == ["limits 512 7", "read"]
    assert json.loads(capsysbinary.readouterr().out) == {
        "parts": ["# Notes"],
        "pages": 1,
    }


def test_running_out_of_memory_while_reading_is_too_large(monkeypatch, capsysbinary):
    """A file too large to read in is ``TooLarge``, not a crash."""
    monkeypatch.setattr(child, "apply_limits", lambda memory, cpu: None)
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
