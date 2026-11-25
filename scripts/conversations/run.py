"""Play the test conversations with Codex and score what it did.

Run it from the repository root::

    python scripts/conversations/run.py --target local
    python scripts/conversations/run.py --target production --only dentist-moved

Each message goes to ``codex exec`` as a person's message. Codex is offered
Reef's tools and nothing else: its shell, web search and other tools are
switched off, and a conversation that still used one is scored ``invalid``.
Later messages of a scenario resume the same Codex session.

Every tool is approved in advance, so the run must never reach anyone's
memory but the made-up person's. Right after the first reset, before any
scenario, the runner asks Codex one question with ``load_index`` as the only
tool, and goes on only when the index Codex reads carries the made-up
person's persona word for word.

Read scenarios run first. Each write scenario starts from a reset, and the
memory is read afterwards to check what the assistant changed. The account
is reset once more at the end, also after a failure, so it rests as the
made-up person. Reports go to ``~/.reef-conversations/reports/``, outside
the repository, and hold only made-up content.

Every ``codex exec`` runs in its own Codex home, ``~/.reef-conversations/codex``
(or ``$REEF_CONVERSATIONS_CODEX_HOME``), set only in the environment of the
processes the runner starts. The person's own ``~/.codex`` is never read or
changed, so their personal ``AGENTS.md`` and settings stay out of the tests.

In production, one sign-in serves the whole run: the Reef command line
tool's, kept in ``~/.reef-conversations/reef-cli`` (or
``$REEF_CONVERSATIONS_REEF_CONFIG_DIR``), never in the person's own
``~/.config/reef``. Every ``reef`` command the runner starts gets that
folder. Before each ``codex exec``, ``reef token`` gives a valid access
token, and Codex gets it in ``REEF_BENCH_TOKEN``, in its own environment
only. The token stays in memory: it is never printed, logged or written.

Every ``codex exec`` uses the Codex quota of whoever runs this.
"""

import argparse
import asyncio
import json
import os
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import local_account
from events import Session, combine, parse_events
from score import Scored, load, score, write_report

from reef.access import Principal
from reef.db import DB, transaction_scope
from reef.engrams import get_engram
from reef.identity import person_by_email
from reef.testaccount import readiness
from reef.testaccount.fixture import read_fixture

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "tests" / "conversations" / "scenarios.toml"
REPORTS = Path.home() / ".reef-conversations" / "reports"

#: Names the folder Codex keeps its sign-in and settings in, for test runs.
CODEX_HOME_VARIABLE = "REEF_CONVERSATIONS_CODEX_HOME"
DEFAULT_CODEX_HOME = (Path.home() / ".reef-conversations" / "codex").resolve()

#: Names the folder the Reef command line tool keeps its sign-in in, for test runs.
CLI_CONFIG_VARIABLE = "REEF_CONVERSATIONS_REEF_CONFIG_DIR"
DEFAULT_CLI_CONFIG = (Path.home() / ".reef-conversations" / "reef-cli").resolve()

#: The variable Codex reads the production access token from.
TOKEN_VARIABLE = "REEF_BENCH_TOKEN"

MODEL = "gpt-6-astra"
LOCAL_EMAIL = "conversations@reef.local"
PRODUCTION_URL = "https://reefwith.me/mcp"

#: Seconds one ``codex exec`` may take, and one reset or read of production.
MESSAGE_TIMEOUT = 420
RESET_TIMEOUT = 360
READY_TIMEOUT = 300
#: Seconds ``reef token`` may take.
TOKEN_TIMEOUT = 120
#: Seconds ``uv`` may take to build the command line tool's environment.
WARM_UP_TIMEOUT = 900

#: Codex features that give the assistant a way to act outside Reef.
_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "plugins",
    "browser_use",
    "computer_use",
    "view_image",
)

#: The Reef command line tool, pinned to production: ``REEF_MCP_URL`` in the
#: shell must not send a reset to one server while Codex plays on another.
_CLI = [
    "uv",
    "run",
    "--project",
    str(ROOT / "clients" / "python"),
    "reef",
    "--url",
    PRODUCTION_URL,
]

#: Reset refusals that mean the command line tool is not signed in as the
#: test account.
_SIGN_IN_REFUSALS = frozenset({"not_a_test_account", "unauthenticated"})

#: The only tools a connector scenario is offered.
CONNECTOR_TOOLS = ("search", "fetch")

#: Variables the local server must not take from the shell: the runner sets
#: them itself.
_SET_BY_RUNNER = frozenset({"REEF_DEV_PRINCIPAL_EMAIL", "REEF_SESSION_SECRET"})


class RunError(Exception):
    """The run cannot go on: a reset failed, or a sign-in is missing."""


class AccountCheckFailed(RunError):
    """Nothing showed that Codex reaches the made-up person's memory."""


@dataclass(frozen=True)
class Server:
    """How Codex reaches Reef.

    :param name: the server's name in the Codex settings; the name the
        events carry back
    :param settings: its ``-c`` settings, as ``key=value`` text
    :param advice: gives what to do when the account check fails, in plain
        words; called when the message is made, so it can name the folder
    :param bearer: whether Codex signs in with the command line tool's access
        token, which the runner passes in ``REEF_BENCH_TOKEN``
    """

    name: str
    settings: tuple[str, ...]
    advice: Callable[[], str]
    bearer: bool = False


def run_secret() -> str:
    """Pick the session secret every local server of one run shares.

    :returns: the shell's ``REEF_SESSION_SECRET`` when it has one, otherwise
        64 random hexadecimal characters
    """
    return os.environ.get("REEF_SESSION_SECRET") or secrets.token_hex(32)


def local_server(secret: str) -> Server:
    """Describe the local server: ``python -m reef.server`` over stdio.

    It runs as the made-up person, from this worktree's Python, and sees
    the ``REEF_*`` and database settings of the shell that started the run.
    Each message starts a server of its own, and a confirmation arrives in
    another one than the call that staged it. So every server of a run gets
    the same session secret, which signs the staged tokens.

    :param secret: the session secret of this run
    :returns: the server
    """
    prefix = "mcp_servers.reef_local"
    forwarded = sorted(
        name
        for name in os.environ
        if (name.startswith("REEF_") or name == "DATABASE_URL")
        and name not in _SET_BY_RUNNER
    )
    return Server(
        "reef_local",
        (
            f"{prefix}.command={json.dumps(sys.executable)}",
            f'{prefix}.args=["-m", "reef.server"]',
            f"{prefix}.cwd={json.dumps(str(ROOT))}",
            f"{prefix}.env.REEF_DEV_PRINCIPAL_EMAIL={json.dumps(LOCAL_EMAIL)}",
            f"{prefix}.env.REEF_SESSION_SECRET={json.dumps(secret)}",
            f"{prefix}.env_vars={json.dumps(forwarded)}",
            f"{prefix}.startup_timeout_sec=60",
        ),
        lambda: (
            f"The local server runs as {LOCAL_EMAIL}. Check that this shell "
            "and the server use the same database, then run "
            "`just conversations-account` and try again."
        ),
    )


PRODUCTION_SERVER = Server(
    "reef_bench",
    (
        f"mcp_servers.reef_bench.url={json.dumps(PRODUCTION_URL)}",
        f"mcp_servers.reef_bench.bearer_token_env_var={json.dumps(TOKEN_VARIABLE)}",
    ),
    lambda: f"Codex uses the sign-in of the Reef command line tool. {switch_advice()}",
    bearer=True,
)


def codex_command(
    server: Server,
    *,
    tools: Sequence[str] | None,
    session_id: str | None,
    workdir: Path | None = None,
) -> list[str]:
    """Build the ``codex exec`` command for one message.

    The message itself is read from standard input. Codex runs read-only,
    without the person's own Codex settings, and with the features that
    reach beyond Reef switched off.

    :param server: the Reef server to offer
    :param tools: the only Reef tools to offer, or None for all of them
    :param session_id: the session to resume, or None for a new one
    :param workdir: the empty directory Codex works in; a new session only
    :returns: the command, ready for ``subprocess``
    """
    command = ["codex", "exec"]
    if session_id is not None:
        command += ["resume", session_id]
    command += ["--json", "-m", MODEL, "--skip-git-repo-check", "--ignore-user-config"]
    command += ["--ignore-rules"]
    if session_id is None:
        command += ["-s", "read-only"]
        if workdir is not None:
            command += ["-C", str(workdir)]
    for feature in _DISABLED_FEATURES:
        command += ["--disable", feature]
    command += ["-c", 'web_search="disabled"', "-c", 'sandbox_mode="read-only"']
    command += ["-c", 'approval_policy="never"']
    for setting in server.settings:
        command += ["-c", setting]
    # By default Codex starts a turn once the server is connected, even when
    # its tool list has not arrived yet. The model then gets no Reef tool and
    # answers from what it can see, such as the name in the home folder.
    command += ["-c", f'mcp_servers.{server.name}.startup_readiness="catalog"']
    # The tools ask the person themselves, in two calls, so Codex does not
    # ask as well: in ``exec`` nobody could answer it.
    command += [
        "-c",
        f'mcp_servers.{server.name}.default_tools_approval_mode="approve"',
    ]
    if tools is not None:
        command += [
            "-c",
            f"mcp_servers.{server.name}.enabled_tools={json.dumps(list(tools))}",
        ]
    return [*command, "-"]


def codex_home() -> Path:
    """Find the folder test conversations use as their Codex home.

    :returns: ``$REEF_CONVERSATIONS_CODEX_HOME`` when set, otherwise
        ``~/.reef-conversations/codex``; never ``~/.codex``
    """
    override = os.environ.get(CODEX_HOME_VARIABLE)
    if not override:
        return DEFAULT_CODEX_HOME
    return Path(override).expanduser().resolve()


def home_prefix() -> str:
    """Write the ``CODEX_HOME=...`` prefix of a command the person types.

    :returns: ``CODEX_HOME=~/.reef-conversations/codex`` for the default
        home, otherwise the shell-quoted path
    """
    home = codex_home()
    shown = (
        "~/.reef-conversations/codex"
        if home == DEFAULT_CODEX_HOME
        else shlex.quote(str(home))
    )
    return f"CODEX_HOME={shown}"


def codex_env(token: str | None = None) -> dict[str, str]:
    """Build the environment of a ``codex`` process the runner starts.

    The runner's own environment is not changed. A ``REEF_BENCH_TOKEN`` or
    ``REEF_ACCESS_TOKEN`` in the shell never reaches Codex: only the token
    given here does.

    :param token: the access token Codex sends to production, or None
    :returns: a copy of the environment with ``CODEX_HOME`` set to the test
        home, and ``REEF_BENCH_TOKEN`` set to the token when there is one
    """
    env = {**os.environ, "CODEX_HOME": str(codex_home())}
    env.pop("REEF_ACCESS_TOKEN", None)
    env.pop(TOKEN_VARIABLE, None)
    if token is not None:
        env[TOKEN_VARIABLE] = token
    return env


def prepare_codex_home() -> Path:
    """Make the test home ready, or say why it cannot be used.

    Refuses your own ``~/.codex``, a folder inside it, or a folder that
    contains it (such as your home folder), before anything is created.
    Creates the folder (readable by its owner only) when it is missing.
    Checks that the runner can create and remove a file in it, as the
    nested ``codex exec`` processes must write there. Refuses a home that
    holds an ``AGENTS.md``, as Codex would load it into every conversation.

    :returns: the test home
    :raises RunError: when the folder is or is inside ``~/.codex``, cannot be
        written, or holds an ``AGENTS.md``
    """
    home = codex_home()
    person = Path.home().resolve()
    own = (person / ".codex").resolve()
    if home in (own, person) or own in home.parents or home in own.parents:
        raise RunError(
            f"The test Codex home {home} is your own Codex home, or a folder "
            "inside it or around it. The test home must be a separate folder, "
            "not your own Codex home or a folder that contains it. "
            f"Unset {CODEX_HOME_VARIABLE} or point it at another folder."
        )
    no_access = RunError(
        f"The folder {home} is not readable and writable. The process that "
        "runs the tests needs read and write access to it."
    )
    try:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=home, prefix=".write-check-"):
            pass
    except OSError as error:
        raise no_access from error
    if (home / "AGENTS.md").exists():
        raise RunError(
            f"The test Codex home {home} holds an AGENTS.md. Codex would "
            "load it into every test conversation. Remove it and run again."
        )
    return home


def codex_login_status() -> tuple[int, str]:
    """Ask Codex whether the test home is signed in.

    :returns: the exit status and the text ``codex login status`` printed
    """
    try:
        done = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            env=codex_env(),
            stdin=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"could not run codex login status: {error}"
    return done.returncode, f"{done.stdout}{done.stderr}".strip()


def check_codex_home() -> Path:
    """Stop unless the test home is usable and signed in to Codex.

    :returns: the test home
    :raises RunError: when the home is not usable, or not signed in
    """
    home = prepare_codex_home()
    status, text = codex_login_status()
    if status != 0 or "not logged in" in text.lower():
        raise RunError(
            "The test home is not signed in to Codex. Run this once, then "
            f"run the tests again: `{home_prefix()} codex login`. "
            "This does not affect your own Codex."
        )
    return home


def cli_config_dir() -> Path:
    """Find the folder the Reef command line tool keeps its test sign-in in.

    :returns: ``$REEF_CONVERSATIONS_REEF_CONFIG_DIR`` when set, otherwise
        ``~/.reef-conversations/reef-cli``
    """
    override = os.environ.get(CLI_CONFIG_VARIABLE)
    if not override:
        return DEFAULT_CLI_CONFIG
    return Path(override).expanduser().resolve()


def cli_prefix() -> str:
    """Write the ``REEF_CONFIG_DIR=...`` prefix of a command the person types.

    :returns: ``REEF_CONFIG_DIR=~/.reef-conversations/reef-cli`` for the
        default folder, otherwise the shell-quoted path
    """
    folder = cli_config_dir()
    shown = (
        "~/.reef-conversations/reef-cli"
        if folder == DEFAULT_CLI_CONFIG
        else shlex.quote(str(folder))
    )
    return f"REEF_CONFIG_DIR={shown}"


def sign_in_advice() -> str:
    """Say how to sign the command line tool in as the test account.

    :returns: the advice, in plain words
    """
    return (
        "Sign the command line tool in as the test account, in a private "
        f"window: `{cli_prefix()} reef login`. Then run the tests again."
    )


def switch_advice() -> str:
    """Say how to replace a sign-in that is not the test account's.

    ``reef login`` keeps a sign-in that still works, so it comes after a
    sign-out.

    :returns: the advice, in plain words
    """
    return f"Sign the tool out with `{cli_prefix()} reef logout`. {sign_in_advice()}"


def _own_cli_folders() -> list[tuple[Path, str]]:
    """List the folders that may hold your own sign-in of the command line tool.

    :returns: each folder, resolved, with how to name it: ``~/.config/reef``,
        and ``$XDG_CONFIG_HOME/reef`` when ``XDG_CONFIG_HOME`` is set
    """
    folders = [
        ((Path.home().resolve() / ".config" / "reef").resolve(), "~/.config/reef")
    ]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        own = (Path(xdg).expanduser().resolve() / "reef").resolve()
        folders.append((own, str(own)))
    return folders


def check_cli_config_dir() -> Path:
    """Refuse your own sign-in folder of the Reef command line tool.

    Refuses ``~/.config/reef`` and, when ``XDG_CONFIG_HOME`` is set,
    ``$XDG_CONFIG_HOME/reef``; a folder inside either; or a folder that
    contains either (such as your home folder). Changes nothing.

    :returns: the test folder
    :raises RunError: when the folder is, is inside or contains one of them
    """
    folder = cli_config_dir()
    for own, shown in _own_cli_folders():
        if folder == own or own in folder.parents or folder in own.parents:
            raise RunError(
                f"The test sign-in folder {folder} is your own sign-in folder "
                f"of the Reef command line tool, {shown}, or a folder inside it "
                "or around it. The test sign-in must be kept in a separate "
                f"folder. Unset {CLI_CONFIG_VARIABLE} or point it at another "
                "folder."
            )
    return folder


def cli_env() -> dict[str, str]:
    """Build the environment of a ``reef`` command the runner starts.

    The runner's own environment is not changed. ``REEF_ACCESS_TOKEN`` is
    left out, so a token in the shell cannot replace the test sign-in.

    :returns: a copy of the environment with ``REEF_CONFIG_DIR`` set to the
        test folder
    """
    env = {**os.environ, "REEF_CONFIG_DIR": str(check_cli_config_dir())}
    env.pop("REEF_ACCESS_TOKEN", None)
    env.pop(TOKEN_VARIABLE, None)
    return env


#: Why the run stops when ``reef token`` fails for another reason than a
#: missing sign-in.
_NO_TOKEN = (
    "The Reef command line tool could not get an access token (a server or "
    "network problem). Try again later."
)


def warm_up_cli() -> None:
    """Let ``uv`` build the command line tool's environment, once, up front.

    On a fresh worktree the first ``reef`` call builds it, which can take
    longer than :data:`TOKEN_TIMEOUT` and would be reported as a server or
    network problem. ``reef --help`` builds it and uses no network. A failure
    is not an error here: the token call that follows reports real problems.
    """
    try:
        subprocess.run(
            [*_CLI[:-2], "--help"],
            capture_output=True,
            text=True,
            env=cli_env(),
            stdin=subprocess.DEVNULL,
            timeout=WARM_UP_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def cli_token() -> str:
    """Get a valid access token for production from the command line tool.

    Runs ``reef token`` with the test folder. The token is good for at least
    as long as one ``codex exec`` may take, when the server allows that. It
    stays in memory: it is never printed, logged or written.

    :returns: the token
    :raises RunError: when the tool gives no token: with the sign-in advice
        when it is not signed in, otherwise with a request to try again
    """
    command = [*_CLI, "token", "--refresh-within", str(MESSAGE_TIMEOUT)]
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=cli_env(),
            stdin=subprocess.DEVNULL,
            timeout=TOKEN_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunError(_NO_TOKEN) from error
    token = done.stdout.strip()
    if done.returncode == 0 and token:
        return token
    # What it printed is never shown: nothing of it may reach a report.
    if _error_code(done.stderr) == "not_signed_in":
        raise RunError(
            "The Reef command line tool has no valid sign-in for the test "
            f"account, so Codex cannot reach production. {sign_in_advice()}"
        )
    raise RunError(_NO_TOKEN)


def _error_code(text: str) -> str | None:
    """Read the code of an error ``reef`` printed last, as ``{"error": code}``.

    :param text: what it printed as an error
    :returns: the code, or None when the last line is not such an error
    """
    lines = text.strip().splitlines()
    try:
        found = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        return None
    code = found.get("error") if isinstance(found, dict) else None
    return code if isinstance(code, str) else None


def _run_codex(
    command: list[str], message: str, workdir: Path, token: str | None = None
) -> Session:
    """Run one ``codex exec`` and read what it printed.

    The token, if Codex printed it anyway, is taken out of what it printed
    before anything is read.

    :param command: the command
    :param message: the person's message
    :param workdir: the directory Codex works in
    :param token: the access token Codex sends to production, or None
    :returns: the session; with an error when Codex failed or timed out
    """
    try:
        done = subprocess.run(
            command,
            input=message,
            capture_output=True,
            text=True,
            cwd=workdir,
            env=codex_env(token),
            timeout=MESSAGE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Session(
            "", [], [], "", f"Codex took longer than {MESSAGE_TIMEOUT} seconds"
        )
    stdout, stderr = done.stdout, done.stderr
    if token:
        stdout = stdout.replace(token, "[token removed]")
        stderr = stderr.replace(token, "[token removed]")
    session = parse_events(stdout.splitlines())
    if done.returncode != 0 and session.error in (None, "the turn did not finish"):
        lines = stderr.strip().splitlines()
        session = replace(session, error=(lines[-1][:200] if lines else "Codex failed"))
    return session


def play(scenario: dict, server: Server, tools: Sequence[str] | None = None) -> Session:
    """Play a scenario's messages, in one Codex session.

    For a server that takes a token, each message gets a fresh one.

    :param scenario: the scenario
    :param server: the Reef server to offer
    :param tools: the only Reef tools to offer; None offers all of them, or
        only ``search`` and ``fetch`` to a connector scenario
    :returns: the conversation: every call in order, every answer
    :raises RunError: when the command line tool gives no token
    """
    if tools is None and scenario["connector"]:
        tools = CONNECTOR_TOOLS
    turns: list[Session] = []
    with tempfile.TemporaryDirectory(prefix="reef-conversation-") as directory:
        workdir = Path(directory)
        session_id: str | None = None
        for message in scenario["messages"]:
            command = codex_command(
                server, tools=tools, session_id=session_id, workdir=workdir
            )
            token = cli_token() if server.bearer else None
            turn = _run_codex(command, message, workdir, token)
            turns.append(turn)
            session_id = session_id or turn.session_id
            if turn.error or not session_id:
                break
    return combine(turns)


def bodies_from_cli(text: str) -> dict[str, str | None]:
    """Read what ``reef open`` printed.

    :param text: the command's standard output
    :returns: each ref's engram body, or None when Reef has no such engram
    """
    output = json.loads(text)
    found = (output.get("structured_content") or output.get("data") or {}).get("result")
    return {
        entry["ref"]: None if "error" in entry else entry.get("body")
        for entry in found or []
        if isinstance(entry, dict) and "ref" in entry
    }


@dataclass(frozen=True)
class Services:
    """What the target can do, after a reset.

    :param files: whether it stores files
    :param file_text: whether it reads the text in files
    :param embedding: whether search has an embedder, when the target says
    :param rerank: whether search reranks, when the target says
    :param fixture_version: the made-up person's version
    :param reef_version: the server's version, when it says
    """

    files: bool
    file_text: bool
    embedding: bool | None = None
    rerank: bool | None = None
    fixture_version: str | None = None
    reef_version: str | None = None


class LocalTarget:
    """The development database, reached in this process."""

    def __init__(self) -> None:
        """Start with no person: the first reset finds them.

        The server description is made here, so every server of one run
        shares one session secret.
        """
        self._principal: Principal | None = None
        self.server = local_server(run_secret())

    async def start(self) -> None:
        """Connect to the database."""
        await DB.start_connection_pool()

    async def stop(self) -> None:
        """Disconnect from the database."""
        await DB.close_connection_pool()

    async def reset(self) -> Services:
        """Reset the made-up person, and wait until search is ready.

        The first call also makes the account.

        :returns: what this database can do
        :raises RunError: when the database is not local or never gets ready
        """
        try:
            _, result = await local_account.prepare_local_account()
        except local_account.evalkit.NotLocal as error:
            raise RunError(str(error)) from error
        identity = await person_by_email(LOCAL_EMAIL)
        self._principal = Principal(person_id=identity.person_id, email=identity.email)
        deadline = time.monotonic() + READY_TIMEOUT
        while not (await readiness(self._principal)).ready:
            if time.monotonic() > deadline:
                raise RunError("search was not ready after the reset")
            await asyncio.sleep(1)
        return Services(
            files=bool(result.services["files"]),
            file_text=bool(result.services["file_text"]),
            embedding=bool(result.services["embedding"]),
            rerank=bool(result.services["rerank"]),
            fixture_version=str(result.fixture_version),
            reef_version=version("reef"),
        )

    async def read(self, refs: list[str]) -> dict[str, str | None]:
        """Read engram bodies.

        :param refs: ``<space>/<path>`` for each engram
        :returns: each ref's body, or None when it does not exist
        """
        bodies: dict[str, str | None] = {}
        for ref in refs:
            space, _, path = ref.partition("/")
            async with transaction_scope():
                engram = await get_engram(self._principal, space, path)
            bodies[ref] = engram.body if engram else None
        return bodies


class ProductionTarget:
    """Reef in production, reached with the Reef command line tool."""

    server = PRODUCTION_SERVER

    async def start(self) -> None:
        """Nothing to connect to."""

    async def stop(self) -> None:
        """Nothing to disconnect from."""

    def _cli(self, *arguments: str, timeout: int) -> str:
        """Run the Reef command line tool, with the test sign-in.

        :param arguments: what to give it
        :param timeout: seconds to wait
        :returns: its standard output
        :raises RunError: when it does not finish
        """
        try:
            done = subprocess.run(
                [*_CLI, *arguments],
                capture_output=True,
                text=True,
                env=cli_env(),
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as error:
            raise RunError(
                "The Reef command line tool did not finish. It may be waiting "
                f"for a sign-in. {sign_in_advice()}"
            ) from error
        return done.stdout

    async def reset(self) -> Services:
        """Reset the test account in production, and wait until it is ready.

        :returns: what production can do
        :raises RunError: when the reset fails
        """
        text = self._cli("reset-test-account", timeout=RESET_TIMEOUT)
        try:
            result = json.loads(text)
        except json.JSONDecodeError as error:
            raise RunError("the reset printed something unreadable") from error
        if "error" in result:
            message = f"the reset failed: {result['error']}"
            if result["error"] in _SIGN_IN_REFUSALS:
                message += (
                    ". Reef resets only the test account, and the command line "
                    f"tool is not signed in as it. {switch_advice()}"
                )
            raise RunError(message)
        services = result["services"]
        return Services(
            files=bool(services["files"]),
            file_text=bool(services["file_text"]),
            embedding=_maybe_bool(services.get("embedding")),
            rerank=_maybe_bool(services.get("rerank")),
            fixture_version=str(result.get("fixture_version")),
            reef_version=str(result.get("reef_version")),
        )

    async def read(self, refs: list[str]) -> dict[str, str | None]:
        """Read engram bodies with ``reef open``.

        :param refs: ``<space>/<path>`` for each engram
        :returns: each ref's body, or None when it does not exist
        :raises RunError: when the output cannot be read
        """
        text = self._cli("open", *refs, timeout=120)
        try:
            return bodies_from_cli(text)
        except (json.JSONDecodeError, AttributeError) as error:
            raise RunError("reef open printed something unreadable") from error


def _maybe_bool(value: object) -> bool | None:
    """Read a yes or no the target may leave out.

    :param value: the value, or None when the target did not say
    :returns: the value as a bool, or None
    """
    return None if value is None else bool(value)


PERSON = "Lode Vanhecke"

#: The account check's one question.
PROBE = {"id": "account-check", "messages": ["Hoe heet ik?"], "connector": False}

#: The only tool the account check offers: it reads, and cannot change
#: anything in whatever memory Codex reaches.
PROBE_TOOLS = ("load_index",)


def account_problem(session: Session, persona: str) -> str | None:
    """Say why a session does not show the made-up person's memory.

    Only the operating protocol of a successful ``load_index`` counts: it
    ends with the persona of the account Codex is signed in to. The made-up
    person's persona must be in it word for word. A name found anywhere
    else proves nothing, as any memory can mention Lode Vanhecke.

    :param session: what Codex did with the account check's question
    :param persona: the made-up person's persona, from the fixture
    :returns: what is wrong, as a sentence; None when every index Codex read
        carries the persona
    """
    if session.error:
        return f"The account check failed, because Codex failed: {session.error}"
    protocols = [
        call.protocol
        for call in session.tool_calls
        if call.tool == "load_index" and call.ok
    ]
    if not protocols:
        return "The account check failed, because Codex did not read the index"
    expected = persona.strip()
    if not expected or not all(expected in protocol for protocol in protocols):
        return (
            "Codex is signed in to another account: the index it read does "
            f"not carry the persona of the made-up person, {PERSON}"
        )
    return None


async def check_account(server: Server, persona: str) -> None:
    """Stop the run unless Codex reaches the made-up person's memory.

    Codex is signed in to whatever account it is signed in to, and every
    tool is approved in advance. So before any scenario, Codex gets one
    question with ``load_index`` as its only tool.

    :param server: the Reef server to check
    :param persona: the made-up person's persona, from the fixture
    :raises AccountCheckFailed: when the index does not carry the persona,
        or Codex did not read it
    """
    session = await asyncio.to_thread(play, PROBE, server, PROBE_TOOLS)
    problem = account_problem(session, persona)
    if problem is not None:
        raise AccountCheckFailed(
            f"{problem}. No scenario was played. {server.advice()}"
        )


async def run(
    target: LocalTarget | ProductionTarget,
    only: list[str] | None,
    file_text: bool | None,
) -> Path:
    """Play the scenarios against a target and write the report.

    The report is written whatever happens, with what was played so far; if
    the run stopped, its meta says so and why. The error that stopped it
    still propagates.

    :param target: where to play
    :param only: the ids to play, or None for all
    :param file_text: whether file text is on, or None to ask the target
    :returns: the Markdown report's path
    :raises RunError: when an id is unknown, a folder or sign-in is not
        usable, or a reset fails
    :raises AccountCheckFailed: when nothing shows that Codex reaches the
        made-up person's memory; no scenario is played then
    """
    scenarios = load(SCENARIOS)
    known = {scenario["id"] for scenario in scenarios}
    unknown = sorted(set(only or []) - known)
    if unknown:
        raise RunError(f"no scenario named {', '.join(unknown)}")
    chosen = [s for s in scenarios if not only or s["id"] in only]
    chosen.sort(key=lambda s: s["kind"] != "read")
    if target.server.bearer:
        check_cli_config_dir()
    home = check_codex_home()
    if target.server.bearer:
        # The first token call must not wait for ``uv`` to build the tool.
        warm_up_cli()
        # Proves the command line tool is signed in before any reset; this
        # token is not used.
        cli_token()
    started = datetime.now(UTC)
    scored: list[Scored] = []
    answers: dict[str, str] = {}
    meta: dict = {
        "target": "local" if isinstance(target, LocalTarget) else "production",
        "started": started.isoformat(timespec="seconds"),
        "model": MODEL,
        "codex_home": str(home),
    }
    persona = read_fixture().persona
    services: Services | None = None
    await target.start()
    try:
        try:
            services = await target.reset()
            text_on = services.file_text if file_text is None else file_text
            meta |= {
                "files": services.files,
                "file_text": text_on,
                "embedding": services.embedding,
                "rerank": services.rerank,
                "fixture_version": services.fixture_version,
                "reef_version": services.reef_version,
            }
            await check_account(target.server, persona)
            meta["account_check"] = "passed"
            for scenario in chosen:
                item, answer = await _play_one(
                    target, scenario, services.files, text_on
                )
                scored.append(item)
                answers[item.id] = answer
                print(f"{item.id}: {item.verdict} ({item.seconds:.0f}s)", flush=True)
        except BaseException as error:
            meta["stopped"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            if services is not None:
                try:
                    await target.reset()
                except Exception as error:  # noqa: BLE001 - reported, not hidden
                    meta["final_reset_failed"] = f"{type(error).__name__}: {error}"
    finally:
        meta["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
        await target.stop()
        stem = f"{started:%Y-%m-%dT%H-%M-%SZ}-{meta['target']}"
        _, markdown = write_report(REPORTS, stem, meta, scored, answers)
        print(f"Report: {markdown}", flush=True)
    if "final_reset_failed" in meta:
        raise RunError(
            f"the final reset failed, so the account may not rest as the "
            f"made-up person: {meta['final_reset_failed']}"
        )
    return markdown


async def _play_one(
    target: LocalTarget | ProductionTarget,
    scenario: dict,
    files: bool,
    file_text: bool,
) -> tuple[Scored, str]:
    """Play one scenario and score it.

    A write scenario starts from a reset, and the memory it changed is read
    before anything else touches it.

    :param target: where to play
    :param scenario: the scenario
    :param files: whether the target stores files
    :param file_text: whether the target reads the text in files
    :returns: the verdict, and the last answer
    """
    skipped = score(scenario, None, {}, files=files, file_text=file_text, seconds=0)
    if skipped.verdict == "skipped":
        return skipped, ""
    if scenario["kind"] == "write":
        await target.reset()
    began = time.monotonic()
    session = await asyncio.to_thread(play, scenario, target.server)
    seconds = time.monotonic() - began
    refs = [check["ref"] for check in scenario["after"]]
    after = await target.read(refs) if refs and not session.error else {}
    item = score(
        scenario, session, after, files=files, file_text=file_text, seconds=seconds
    )
    return item, session.answer


def main(argv: list[str] | None = None) -> int:
    """Run from the command line.

    :param argv: the arguments, or None for ``sys.argv``
    :returns: the process status
    """
    parser = argparse.ArgumentParser(
        description="Play the test conversations with Codex."
    )
    parser.add_argument("--target", choices=["local", "production"], required=True)
    parser.add_argument(
        "--only", nargs="+", metavar="ID", help="play only these scenarios"
    )
    parser.add_argument(
        "--file-text", choices=["on", "off"], help="override what the reset says"
    )
    args = parser.parse_args(argv)
    target = LocalTarget() if args.target == "local" else ProductionTarget()
    file_text = None if args.file_text is None else args.file_text == "on"
    try:
        asyncio.run(run(target, args.only, file_text))
    except RunError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
