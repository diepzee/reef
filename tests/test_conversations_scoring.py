"""Test conversations: reading Codex's record, scoring it, and writing the report.

The sample in ``tests/conversations/sample-events.jsonl`` is a real Codex
0.167 record of one question to the local test account; everything in it is
made up.
"""

import importlib.util
import json
import re
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from events import Session, ToolCall, combine, parse_events
from score import Scored, coverage, load, score, write_report

from reef.sealing import seal
from reef.server import mcp
from reef.testaccount import Readiness
from reef.testaccount.fixture import read_fixture


def _load_runner():
    """Load ``scripts/conversations/run.py`` under its own name.

    ``scripts/eval`` has a ``run.py`` too, and comes first on the path.

    :returns: the module
    """
    path = Path(__file__).parents[1] / "scripts" / "conversations" / "run.py"
    spec = importlib.util.spec_from_file_location("conversations_run", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run = _load_runner()
HERE = Path(__file__).parent / "conversations"
SAMPLE = HERE / "sample-events.jsonl"
SCENARIOS = HERE / "scenarios.toml"

SIGNED_URL = (
    "https://files.example.com/abc?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=KEY%2F20261122&X-Amz-Expires=300&X-Amz-Signature=deadbeef01"
)
SHARE_NONCE = "0f8e3c3a-5b7d-4e8f-9a1b-2c3d4e5f6a7b"


def _call(tool: str, ok: bool = True) -> ToolCall:
    """Build one call to the local Reef server.

    :param tool: the tool's name
    :param ok: whether the call succeeded
    :returns: the call
    """
    return ToolCall(server="reef_local", tool=tool, arguments={}, ok=ok)


def _session(
    *tools: str, answer: str = "", other_tools: list[str] | None = None
) -> Session:
    """Build a session that called these Reef tools and gave this answer.

    :param tools: the Reef tools called, in order
    :param answer: the final answer
    :param other_tools: tools outside Reef that were used
    :returns: the session
    """
    return Session(
        session_id="thread-1",
        tool_calls=[_call(tool) for tool in tools],
        other_tools=other_tools or [],
        answer=answer,
    )


def _scenario(**fields: object) -> dict:
    """Build a read scenario about the dentist, with fields replaced.

    :param fields: fields to set or replace
    :returns: the scenario
    """
    return {
        "id": "dentist",
        "kind": "read",
        "messages": ["wanneer moet ik naar de tandarts?"],
        "tools_required": ["load_index", "recall"],
        "tools_forbidden": [],
        "facts": ["3 december", ["9:15", "09:15"]],
        "engrams": ["profile/health.md"],
        "needs": [],
        "connector": False,
        "after": [],
    } | fields


def _score(
    scenario: dict,
    session: Session | None,
    after: dict[str, str | None] | None = None,
    *,
    files: bool = True,
    file_text: bool = True,
) -> Scored:
    """Score with every service on unless a test turns one off.

    :param scenario: the scenario
    :param session: what Codex did
    :param after: what the memory held afterwards, by ref
    :param files: whether the target stores files
    :param file_text: whether the target reads text in files
    :returns: the scored scenario
    """
    return score(
        scenario, session, after or {}, files=files, file_text=file_text, seconds=1.5
    )


RIGHT_ANSWER = (
    "Je controle staat op donderdag **3 december** 2026 om 09:15 "
    "([[personal:profile/health.md]])."
)


def test_parse_sample():
    """The recorded sample gives a session id, Reef calls and an answer."""
    session = parse_events(SAMPLE.read_text(encoding="utf-8").splitlines())
    assert session.session_id
    assert [call.tool for call in session.tool_calls] == [
        "load_index",
        "recall",
        "open",
    ]
    assert all(call.server == "reef_local" and call.ok for call in session.tool_calls)
    assert session.tool_calls[2].arguments["refs"] == ["personal/logs/training.md"]
    assert session.other_tools == []
    assert session.error is None
    assert "82.4 kg" in session.answer


def test_other_items_are_other_tools():
    """A shell command, a file edit and another server's tool are not Reef's."""
    lines = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.completed", "item": {"id": "1", "type": "command_execution"}},
        {"type": "item.completed", "item": {"id": "2", "type": "file_change"}},
        {
            "type": "item.completed",
            "item": {
                "id": "3",
                "type": "mcp_tool_call",
                "server": "linear",
                "tool": "x",
            },
        },
        {"type": "item.completed", "item": {"id": "4", "type": "reasoning"}},
        {
            "type": "item.completed",
            "item": {"id": "5", "type": "agent_message", "text": "ok"},
        },
        {"type": "turn.completed"},
    ]
    session = parse_events(json.dumps(line) for line in lines)
    assert session.other_tools == ["shell", "file_change", "linear.x"]
    assert session.tool_calls == []


def test_failed_turn_is_an_error():
    """A turn that failed, or never finished, is recorded as Codex's error."""
    failed = parse_events(
        [
            json.dumps({"type": "thread.started", "thread_id": "t"}),
            json.dumps({"type": "turn.failed", "error": {"message": "quota"}}),
        ]
    )
    unfinished = parse_events(
        [json.dumps({"type": "thread.started", "thread_id": "t"})]
    )
    assert failed.error == "quota"
    assert unfinished.error
    assert _score(_scenario(), failed).verdict == "invalid"


def test_combine_keeps_every_turn():
    """Turns of one conversation add up: calls in order, each turn's answer."""
    first = _session("load_index", "forget", answer="Zeker?")
    second = _session("forget", answer="Verwijderd.")
    both = combine([first, second])
    assert [call.tool for call in both.tool_calls] == ["load_index", "forget", "forget"]
    assert "Zeker?" in both.answer and "Verwijderd." in both.answer
    assert both.session_id == "thread-1"


def test_right_answer_passes():
    """The right tools, every fact and every engram named: pass."""
    scored = _score(_scenario(), _session("load_index", "recall", answer=RIGHT_ANSWER))
    assert scored.verdict == "pass", scored.reasons
    assert scored.tools == ["load_index", "recall"]
    assert scored.seconds == 1.5


def test_no_session_is_invalid():
    """Without a session there is nothing to score."""
    assert _score(_scenario(), None).verdict == "invalid"


def test_shell_use_is_invalid():
    """A tool outside Reef makes the run invalid, even with every fact present."""
    session = _session(
        "load_index", "recall", answer=RIGHT_ANSWER, other_tools=["shell"]
    )
    scored = _score(_scenario(), session)
    assert scored.verdict == "invalid"
    assert any("shell" in reason for reason in scored.reasons)


def test_missing_required_tool_fails():
    """Answering without a required tool fails."""
    scored = _score(_scenario(), _session("load_index", answer=RIGHT_ANSWER))
    assert scored.verdict == "fail"
    assert any("recall" in reason for reason in scored.reasons)


def test_forbidden_tool_fails():
    """Using a forbidden tool fails."""
    scenario = _scenario(tools_forbidden=["write"])
    session = _session("load_index", "recall", "write", answer=RIGHT_ANSWER)
    assert _score(scenario, session).verdict == "fail"


def test_after_check_fails():
    """Memory that does not hold what it must afterwards fails."""
    scenario = _scenario(
        kind="write",
        after=[
            {
                "ref": "personal/household/car.md",
                "contains": [["12.410", "12410"]],
                "absent": ["11.840"],
            }
        ],
    )
    session = _session("load_index", "recall", answer=RIGHT_ANSWER)
    unchanged = {"personal/household/car.md": "Kilometerstand: 11.840."}
    updated = {"personal/household/car.md": "Kilometerstand: 12.410."}
    kept_old = {"personal/household/car.md": "12.410 (was 11.840)."}
    assert _score(scenario, session, unchanged).verdict == "fail"
    assert _score(scenario, session, kept_old).verdict == "fail"
    assert _score(scenario, session, updated).verdict == "pass"
    assert _score(scenario, session, {}).verdict == "fail"


def test_absent_engram():
    """``absent = true`` asks for the engram to be gone."""
    scenario = _scenario(
        kind="write", after=[{"ref": "personal/notes/gift-ideas.md", "absent": True}]
    )
    session = _session("load_index", "recall", answer=RIGHT_ANSWER)
    gone = {"personal/notes/gift-ideas.md": None}
    there = {"personal/notes/gift-ideas.md": "# Cadeaus"}
    assert _score(scenario, session, gone).verdict == "pass"
    assert _score(scenario, session, there).verdict == "fail"


def test_file_text_skipped():
    """A scenario that needs file text is skipped where reading is off."""
    scenario = _scenario(needs=["file_text"])
    scored = _score(scenario, None, file_text=False)
    assert scored.verdict == "skipped"


def test_files_skipped():
    """A scenario that needs stored files is skipped where there are none."""
    scenario = _scenario(needs=["files"])
    assert _score(scenario, None, files=False).verdict == "skipped"


def test_fact_phrased_otherwise_is_review():
    """With the right tools but a fact not found, a person judges it."""
    answer = "Je controle is op donderdag de derde december, om kwart over negen."
    scored = _score(_scenario(), _session("load_index", "recall", answer=answer))
    assert scored.verdict == "review"
    assert any("3 december" in reason for reason in scored.reasons)


def test_facts_ignore_case_space_and_emphasis():
    """Facts match whatever the case, the spacing or the bold marks."""
    answer = "Op Donderdag  **3 December** om 09:15, zie profile/health.md."
    scored = _score(_scenario(), _session("load_index", "recall", answer=answer))
    assert scored.verdict == "pass", scored.reasons


async def test_all_tools_covered():
    """Together the scenarios require every one of Reef's tools."""
    tools = {tool.name for tool in await mcp.list_tools()}
    assert coverage(load(SCENARIOS)) == tools
    assert len(tools) == 16


def test_scenarios_are_well_formed():
    """Reads come before writes, ids are unique, and writes check the memory."""
    scenarios = load(SCENARIOS)
    assert 25 <= len(scenarios) <= 35
    ids = [scenario["id"] for scenario in scenarios]
    assert len(ids) == len(set(ids))
    kinds = [scenario["kind"] for scenario in scenarios]
    assert kinds == sorted(kinds)  # "read" sorts before "write"
    for scenario in scenarios:
        if (
            scenario["kind"] == "write"
            and "store_file" not in scenario["tools_required"]
        ):
            assert scenario["after"], scenario["id"]


def test_report_drops_tokens(tmp_path):
    """The report keeps the answer but not a staged token, a nonce or a signed link."""
    token = seal({"action": "forget"}, secret="s" * 32, ttl_seconds=600)
    answer = (
        f"Bevestig met {token} of {SHARE_NONCE}. Het bestand: {SIGNED_URL} . "
        "De controle is op 3 december."
    )
    scored = Scored(
        id="dentist",
        verdict="review",
        reasons=[f"saw {token}"],
        tools=["open"],
        seconds=2.0,
    )
    paths = write_report(
        tmp_path,
        "2026-11-22T10-00-00Z-local",
        {"target": "local"},
        [scored],
        {"dentist": answer},
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert token not in text
        assert token.split(".")[0] not in text
        assert SHARE_NONCE not in text
        assert "X-Amz-Signature" not in text and "files.example.com" not in text
        assert "3 december" in text
    report = json.loads(paths[0].read_text(encoding="utf-8"))
    assert report["scenarios"][0]["verdict"] == "review"
    assert report["totals"] == {"review": 1}


def test_codex_command_offers_only_reef(tmp_path):
    """Codex runs read-only, without its shell or the person's own settings."""
    server = run.local_server("ab" * 32)
    first = run.codex_command(server, tools=None, session_id=None)
    again = run.codex_command(server, tools=run.CONNECTOR_TOOLS, session_id="t-1")
    probe = run.codex_command(server, tools=run.PROBE_TOOLS, session_id=None)
    for command in (first, again, probe):
        assert "--json" in command and "--ignore-user-config" in command
        assert "shell_tool" in command and 'web_search="disabled"' in command
        assert any(
            part.startswith("mcp_servers.reef_local.command=") for part in command
        )
        assert command[-1] == "-"
    assert ["-s", "read-only"] == first[first.index("-s") : first.index("-s") + 2]
    assert again[again.index("resume") + 1 : again.index("resume") + 2] == ["t-1"]
    assert 'mcp_servers.reef_local.enabled_tools=["search", "fetch"]' in again
    assert 'mcp_servers.reef_local.enabled_tools=["load_index"]' in probe
    assert not any("enabled_tools" in part for part in first)


def test_reads_cli_output():
    """What ``reef open`` prints is read back by ref."""
    opened = {
        "structured_content": {
            "result": [
                {"ref": "personal/a.md", "kind": "engram", "body": "# A"},
                {"ref": "personal/b.md", "error": "not_found"},
            ]
        }
    }
    assert run.bodies_from_cli(json.dumps(opened)) == {
        "personal/a.md": "# A",
        "personal/b.md": None,
    }


def test_failed_required_call_does_not_count():
    """A required tool whose calls all failed is missing, and the call is listed."""
    session = Session(
        "t",
        [_call("load_index"), _call("recall", ok=False)],
        [],
        RIGHT_ANSWER,
    )
    scored = _score(_scenario(), session)
    assert scored.verdict == "fail"
    assert any("recall" in reason and "failed" in reason for reason in scored.reasons)
    retried = Session(
        "t",
        [_call("load_index"), _call("recall", ok=False), _call("recall")],
        [],
        RIGHT_ANSWER,
    )
    passed = _score(_scenario(), retried)
    assert passed.verdict == "pass"
    assert passed.reasons == ["the call to recall failed"]


def test_declined_forget_must_ask_forget():
    """Declining a deletion passes only when ``forget`` showed the effect first."""
    scenario = next(s for s in load(SCENARIOS) if s["id"] == "forget-declined")
    kept = {"personal/notes/gift-ideas.md": "- Sofie (Kerstmis): loopjas."}
    failed = Session(
        "t",
        [_call("load_index"), _call("open"), _call("forget", ok=False)],
        [],
        "Ik heb niets verwijderd.",
    )
    scored = _score(scenario, failed, kept)
    assert scored.verdict == "fail"
    assert "the call to forget failed" in scored.reasons
    asked = Session(
        "t",
        [_call("load_index"), _call("forget")],
        [],
        "Zeker? Goed, ik laat ze staan.",
    )
    assert _score(scenario, asked, kept).verdict == "pass"


def test_confirming_before_the_person_says_yes_fails():
    """A ``confirm`` argument in the first turn of a two-message scenario fails."""
    scenario = _scenario(
        tools_required=["share"],
        messages=["Deel dit.", "Ja, doe maar."],
        facts=[],
        engrams=[],
    )
    early = ToolCall("reef_local", "share", {"confirm": "tok"}, True, turn=0)
    late = ToolCall("reef_local", "share", {"confirm": "tok"}, True, turn=1)
    first = ToolCall("reef_local", "share", {}, True, turn=0)
    bad = Session("t", [early], [], "ok")
    good = Session("t", [first, late], [], "ok")
    assert _score(scenario, bad).verdict == "fail"
    assert _score(scenario, good).verdict == "pass"


def test_combine_numbers_the_turns():
    """Each call carries the number of the turn it was made in."""
    both = combine([_session("load_index"), _session("share", "share")])
    assert [call.turn for call in both.tool_calls] == [0, 1, 1]


def test_not_in_memory_is_never_a_plain_pass():
    """The scenario is a review at best, and an invented number fails it."""
    scenario = next(s for s in load(SCENARIOS) if s["id"] == "not-in-memory")
    plain = _session("load_index", "recall", answer="Daar heb ik niets over.")
    invented = _session("load_index", "recall", answer="Het is 88.03.14-123.45.")
    assert _score(scenario, plain).verdict == "review"
    assert _score(scenario, invented).verdict == "fail"


def test_tyre_check_tells_old_from_new():
    """The unchanged engram does not pass; the edited one does, with a dated note."""
    scenario = next(s for s in load(SCENARIOS) if s["id"] == "fix-tyre-pressure")
    session = _session("load_index", "correct")
    ref = "personal/howto/bike-tire.md"
    old = "Tyre pressure of my city bike: 4.5 bar rear, 4 bar front."
    new = "Tyre pressure: 5 bar rear, 4.5 bar front (changed 2026-11-22; was 4.5 rear)."
    assert _score(scenario, session, {ref: old}).verdict == "fail"
    assert _score(scenario, session, {ref: new}).verdict == "pass"


def _index_call(persona: str, *, ok: bool = True, structured: bool = True) -> dict:
    """Build the event of a completed ``load_index`` call, as Codex prints it.

    Every engram line in the index names Lode Vanhecke, whatever the persona.

    :param persona: the persona at the end of the operating protocol
    :param ok: whether the call succeeded
    :param structured: whether the result carries structured content; without
        it the protocol is only in the text part
    :returns: the ``item.completed`` event
    """
    payload = {
        "operating_protocol": f"# Operating protocol\n\nRead it.\n\n---\n\n{persona}",
        "spaces": [
            {
                "alias": "personal",
                "engrams": [
                    "profile/about-lode.md | Over Lode Vanhecke | Lode Vanhecke"
                ],
            }
        ],
    }
    result: dict = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    if structured:
        result["structured_content"] = payload
    return {
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "mcp_tool_call",
            "server": "reef_test",
            "tool": "load_index",
            "arguments": {},
            "result": result,
            "error": None if ok else {"message": "refused"},
            "status": "completed" if ok else "failed",
        },
    }


def _probe(*events: dict) -> Session:
    """Parse an account check's events, as one finished turn.

    :param events: the item events
    :returns: the session
    """
    lines = [
        {"type": "thread.started", "thread_id": "t"},
        *events,
        {"type": "turn.completed"},
    ]
    return parse_events(json.dumps(line) for line in lines)


#: A persona that names the made-up person but belongs to someone else.
OTHER_PERSONA = (
    "# Persona\n\nJe werkt voor Jan Peeters. Zijn buurman heet Lode Vanhecke."
)


def test_account_check_reads_the_persona():
    """Only the made-up person's persona, word for word, passes the check."""
    persona = read_fixture().persona
    sample = parse_events(SAMPLE.read_text(encoding="utf-8").splitlines())
    assert run.account_problem(sample, persona) is None
    assert run.account_problem(_probe(_index_call(persona)), persona) is None
    text_only = _probe(_index_call(persona, structured=False))
    assert run.account_problem(text_only, persona) is None
    named = run.account_problem(_probe(_index_call(OTHER_PERSONA)), persona)
    assert named and "another account" in named
    failed = run.account_problem(_probe(_index_call(persona, ok=False)), persona)
    assert failed and "did not read the index" in failed
    assert run.account_problem(_probe(), persona)
    broken = Session("t", [], [], "", "quota")
    assert "quota" in (run.account_problem(broken, persona) or "")


class _FakeTarget:
    """A target that resets and reads nothing, to test the run's bookkeeping."""

    def __init__(
        self,
        server: run.Server | None = None,
        services: run.Services | None = None,
        fail_final_reset: bool = False,
    ) -> None:
        """Remember the server, the services and whether later resets fail.

        :param server: the server Codex is offered; a local one by default
        :param services: what each reset reports; everything on by default
        :param fail_final_reset: make every reset after the first fail
        """
        self.server = server or run.local_server("ab" * 32)
        self.services = services or run.Services(files=True, file_text=True)
        self.resets = 0
        self.fail_final_reset = fail_final_reset

    async def start(self) -> None:
        """Do nothing."""

    async def stop(self) -> None:
        """Do nothing."""

    async def reset(self) -> run.Services:
        """Count the reset, and fail after the first when asked.

        :returns: the services
        """
        self.resets += 1
        if self.fail_final_reset and self.resets > 1:
            raise run.RunError("reset broke")
        return self.services

    async def read(self, refs: list[str]) -> dict[str, str | None]:
        """Read nothing.

        :param refs: ignored
        :returns: no bodies
        """
        return {}


class _Recorder:
    """Stands in for :func:`run.play`: notes each conversation, answers with a session."""

    def __init__(self, probe: Session) -> None:
        """Answer the account check with ``probe``, and scenarios with the sample.

        :param probe: what Codex does with the account check's question
        """
        self.probe = probe
        self.played: list[tuple[str, tuple[str, ...] | None]] = []

    def __call__(
        self, scenario: dict, server: run.Server, tools: tuple[str, ...] | None = None
    ) -> Session:
        """Note the scenario and the tools offered, and answer.

        :param scenario: the scenario played
        :param server: ignored
        :param tools: the only tools offered, if limited
        :returns: the session
        """
        self.played.append((scenario["id"], tools))
        if scenario["id"] == run.PROBE["id"]:
            return self.probe
        return parse_events(SAMPLE.read_text(encoding="utf-8").splitlines())


def _report(directory: Path) -> dict:
    """Read the one JSON report a run wrote.

    :param directory: where the report went
    :returns: the report
    """
    return json.loads(next(directory.glob("*.json")).read_text(encoding="utf-8"))


async def test_account_check_runs_before_the_first_scenario(tmp_path, monkeypatch):
    """The check, with ``load_index`` alone, comes before even a read scenario."""
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    recorder = _Recorder(_probe(_index_call(read_fixture().persona)))
    monkeypatch.setattr(run, "play", recorder)
    await run.run(_FakeTarget(), ["dentist-moved"], None)
    assert recorder.played == [
        ("account-check", ("load_index",)),
        ("dentist-moved", None),
    ]
    assert _report(tmp_path)["meta"]["account_check"] == "passed"


async def test_refused_account_check_plays_no_scenario(tmp_path, monkeypatch):
    """Another person's index stops the run before any scenario, and says what to do."""
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    recorder = _Recorder(_probe(_index_call(OTHER_PERSONA)))
    monkeypatch.setattr(run, "play", recorder)
    target = _FakeTarget(server=run.PRODUCTION_SERVER)
    with pytest.raises(run.AccountCheckFailed, match="another account"):
        await run.run(target, ["dentist-moved", "forget-confirmed"], None)
    assert recorder.played == [("account-check", ("load_index",))]
    report = _report(tmp_path)
    assert report["scenarios"] == []
    assert "No scenario was played" in report["meta"]["stopped"]
    assert (
        "codex mcp logout reef_test -c "
        "'mcp_servers.reef_test.url=\"https://reefwith.me/mcp\"'"
    ) in report["meta"]["stopped"]
    assert target.resets == 2


async def test_local_servers_of_a_run_share_one_secret(tmp_path, monkeypatch):
    """Every local server of a run, the account check's too, gets one session secret."""
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    monkeypatch.delenv("REEF_SESSION_SECRET", raising=False)
    sample = parse_events(SAMPLE.read_text(encoding="utf-8").splitlines())
    commands: list[list[str]] = []

    def fake_codex(command: list[str], message: str, workdir: Path) -> Session:
        """Note the command and answer with the sample.

        :param command: the ``codex exec`` command
        :param message: ignored
        :param workdir: ignored
        :returns: the sample session
        """
        commands.append(command)
        return sample

    monkeypatch.setattr(run, "_run_codex", fake_codex)
    target = _FakeTarget(server=run.LocalTarget().server)
    await run.run(target, ["dentist-moved", "forget-confirmed"], None)
    prefix = "mcp_servers.reef_local.env.REEF_SESSION_SECRET="
    found = {
        json.loads(part.removeprefix(prefix))
        for command in commands
        for part in command
        if part.startswith(prefix)
    }
    assert len(commands) == 4  # the check, one read, two messages of a write
    assert all(any(p.startswith(prefix) for p in command) for command in commands)
    assert len(found) == 1
    assert re.fullmatch(r"[0-9a-f]{64}", found.pop())
    assert run.LocalTarget().server != target.server
    monkeypatch.setenv("REEF_SESSION_SECRET", "s" * 40)
    assert f"{prefix}{json.dumps('s' * 40)}" in run.LocalTarget().server.settings
    forwarded = next(
        part for part in run.LocalTarget().server.settings if ".env_vars=" in part
    )
    assert "REEF_SESSION_SECRET" not in forwarded


async def test_report_names_search_services(tmp_path, monkeypatch):
    """The report says whether search has an embedder and reranks."""
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    monkeypatch.setattr(
        run, "play", _Recorder(_probe(_index_call(read_fixture().persona)))
    )
    services = run.Services(
        files=False, file_text=False, embedding=True, rerank=False, reef_version="9.9"
    )
    await run.run(_FakeTarget(services=services), ["dentist-moved"], None)
    meta = _report(tmp_path)["meta"]
    assert (meta["embedding"], meta["rerank"], meta["reef_version"]) == (
        True,
        False,
        "9.9",
    )
    markdown = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "embedding: True" in markdown and "rerank: False" in markdown


async def test_local_reset_names_its_services_and_version(seed, monkeypatch):
    """A local reset gives the search services and the installed Reef version."""

    async def ready(principal: object) -> Readiness:
        """Say the memory is ready.

        :param principal: ignored
        :returns: ready, with nothing waiting
        """
        return Readiness(ready=True, vectors_missing=0, files_unread=0, jobs_waiting=0)

    monkeypatch.setattr(run, "readiness", ready)
    services = await run.LocalTarget().reset()
    assert services.reef_version == version("reef")
    assert isinstance(services.embedding, bool) and isinstance(services.rerank, bool)


def test_production_cli_names_its_server():
    """Every call of the Reef command line tool names production itself."""
    assert run._CLI[-2:] == ["--url", run.PRODUCTION_URL]


async def test_failed_run_still_writes_a_report(tmp_path, monkeypatch):
    """An error mid-run leaves a report that says why, and still propagates."""
    monkeypatch.setattr(run, "REPORTS", tmp_path)

    def boom(scenario: dict, server: run.Server, tools: object = None) -> Session:
        """Fail as a broken Codex would.

        :param scenario: ignored
        :param server: ignored
        :param tools: ignored
        :raises RuntimeError: always
        """
        raise RuntimeError("boom")

    monkeypatch.setattr(run, "play", boom)
    target = _FakeTarget(fail_final_reset=True)
    with pytest.raises(RuntimeError, match="boom"):
        await run.run(target, ["dentist-moved"], None)
    report = _report(tmp_path)
    assert "boom" in report["meta"]["stopped"]
    assert "reset broke" in report["meta"]["final_reset_failed"]
