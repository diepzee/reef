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

Every ``codex exec`` uses the Codex quota of whoever runs this.
"""

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
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

MODEL = "gpt-6-astra"
LOCAL_EMAIL = "conversations@reef.local"
PRODUCTION_URL = "https://reefwith.me/mcp"

#: Seconds one ``codex exec`` may take, and one reset or read of production.
MESSAGE_TIMEOUT = 420
RESET_TIMEOUT = 360
READY_TIMEOUT = 300

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
    :param advice: what to do when the account check fails, in plain words
    """

    name: str
    settings: tuple[str, ...]
    advice: str


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
        (
            f"The local server runs as {LOCAL_EMAIL}. Check that this shell "
            "and the server use the same database, then run "
            "`just conversations-account` and try again."
        ),
    )


PRODUCTION_SERVER = Server(
    "reef_test",
    (f"mcp_servers.reef_test.url={json.dumps(PRODUCTION_URL)}",),
    (
        "Sign Codex out with `codex mcp logout reef_test -c "
        f"'mcp_servers.reef_test.url={json.dumps(PRODUCTION_URL)}'`. Then sign "
        "it in again as the test account, as step 2 of the one-time "
        "production setup in docs/runbook.md says."
    ),
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


def _run_codex(command: list[str], message: str, workdir: Path) -> Session:
    """Run one ``codex exec`` and read what it printed.

    :param command: the command
    :param message: the person's message
    :param workdir: the directory Codex works in
    :returns: the session; with an error when Codex failed or timed out
    """
    try:
        done = subprocess.run(
            command,
            input=message,
            capture_output=True,
            text=True,
            cwd=workdir,
            timeout=MESSAGE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return Session(
            "", [], [], "", f"Codex took longer than {MESSAGE_TIMEOUT} seconds"
        )
    session = parse_events(done.stdout.splitlines())
    if done.returncode != 0 and session.error in (None, "the turn did not finish"):
        lines = done.stderr.strip().splitlines()
        session = replace(session, error=(lines[-1][:200] if lines else "Codex failed"))
    return session


def play(scenario: dict, server: Server, tools: Sequence[str] | None = None) -> Session:
    """Play a scenario's messages, in one Codex session.

    :param scenario: the scenario
    :param server: the Reef server to offer
    :param tools: the only Reef tools to offer; None offers all of them, or
        only ``search`` and ``fetch`` to a connector scenario
    :returns: the conversation: every call in order, every answer
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
            turn = _run_codex(command, message, workdir)
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
        """Run the Reef command line tool.

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
                timeout=timeout,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as error:
            raise RunError(
                "The Reef command line tool did not finish. It may be waiting "
                "for a sign-in. Sign in first with `uv run --project "
                f"clients/python reef --url {PRODUCTION_URL} login`, then run "
                "this again."
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
            raise RunError(f"the reset failed: {result['error']}")
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
        raise AccountCheckFailed(f"{problem}. No scenario was played. {server.advice}")


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
    :raises RunError: when an id is unknown or a reset fails
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
    started = datetime.now(UTC)
    scored: list[Scored] = []
    answers: dict[str, str] = {}
    meta: dict = {
        "target": "local" if isinstance(target, LocalTarget) else "production",
        "started": started.isoformat(timespec="seconds"),
        "model": MODEL,
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
