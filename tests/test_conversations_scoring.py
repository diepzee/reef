"""Test conversations: reading Codex's record, scoring it, and writing the report.

The sample in ``tests/conversations/sample-events.jsonl`` is a real Codex
0.167 record of one question to the local test account; everything in it is
made up.
"""

import asyncio
import importlib.util
import json
import os
import re
import shlex
import subprocess
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


def test_replace_contract_scenario_asks_then_confirms_then_reads() -> None:
    """The scripted conversation changes a relation and asks for the new fact."""
    scenario = next(
        scenario for scenario in load(SCENARIOS) if scenario["id"] == "replace-contract"
    )
    assert scenario["kind"] == "write"
    assert "write" in scenario["tools_required"]
    assert len(scenario["messages"]) == 3
    assert scenario["messages"][1].startswith("Ja")
    assert scenario["facts"] == [["12 uur", "twaalf uur"]]
    assert len(scenario["after"]) == 2
    assert (
        scenario["messages"][-1]
        == "Binnen hoeveel uur moet Archiefhaven reageren op een storing?"
    )
    assert scenario["answer_must_not"]
    assert scenario["after"][0]["replaced_by"] == ["personal/work/contract-2027.md"]


def test_after_checks_require_the_replacement_link():
    """An answer and both texts cannot pass while the replacement link is absent."""
    scenario = _scenario(
        kind="write",
        after=[
            {
                "ref": "personal/old.md",
                "contains": ["old"],
                "replaced_by": ["personal/new.md"],
            }
        ],
    )
    session = _session("load_index", "recall", answer=RIGHT_ANSWER)
    assert _score(scenario, session, {"personal/old.md": "old"}).verdict == "fail"
    after = {
        "personal/old.md": {
            "body": "old",
            "replaced_by": [{"space": "personal", "path": "new.md", "title": "New"}],
        }
    }
    assert _score(scenario, session, after).verdict == "pass"


def test_cli_reads_keep_replacement_references():
    """The conversation's after checks receive the note returned by open."""
    note = {
        "body": "old",
        "replaced_by": [{"space": "personal", "path": "new.md", "title": "New"}],
    }
    output = {"structured_content": {"result": [{"ref": "personal/old.md", **note}]}}
    assert run.bodies_from_cli(json.dumps(output)) == {"personal/old.md": note}


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
            "server": "reef_bench",
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
    monkeypatch.setattr(run, "cli_token", lambda: "bench-token-0")
    monkeypatch.delenv(run.CLI_CONFIG_VARIABLE)
    monkeypatch.setattr(run, "DEFAULT_CLI_CONFIG", tmp_path / "default-cli")
    recorder = _Recorder(_probe(_index_call(OTHER_PERSONA)))
    monkeypatch.setattr(run, "play", recorder)
    target = _FakeTarget(server=run.PRODUCTION_SERVER)
    with pytest.raises(run.AccountCheckFailed, match="another account"):
        await run.run(target, ["dentist-moved", "forget-confirmed"], None)
    assert recorder.played == [("account-check", ("load_index",))]
    report = _report(tmp_path)
    assert report["scenarios"] == []
    stopped = report["meta"]["stopped"]
    assert "No scenario was played" in stopped
    assert "REEF_CONFIG_DIR=~/.reef-conversations/reef-cli reef logout" in stopped
    assert "REEF_CONFIG_DIR=~/.reef-conversations/reef-cli reef login" in stopped
    assert "private window" in stopped
    assert "reef_test" not in stopped
    assert target.resets == 2


async def test_local_servers_of_a_run_share_one_secret(tmp_path, monkeypatch):
    """Every local server of a run, the account check's too, gets one session secret."""
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    monkeypatch.delenv("REEF_SESSION_SECRET", raising=False)
    sample = parse_events(SAMPLE.read_text(encoding="utf-8").splitlines())
    commands: list[list[str]] = []

    def fake_codex(
        command: list[str], message: str, workdir: Path, token: str | None = None
    ) -> Session:
        """Note the command and answer with the sample.

        :param command: the ``codex exec`` command
        :param message: ignored
        :param workdir: ignored
        :param token: the access token; None for the local server
        :returns: the sample session
        """
        assert token is None
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


_REAL_WARM_UP = run.warm_up_cli


@pytest.fixture(autouse=True)
def _no_warm_up(monkeypatch):
    """Keep ``uv`` out of the tests: the warm-up does nothing unless a test asks.

    :param monkeypatch: pytest's monkeypatch
    """
    monkeypatch.setattr(run, "warm_up_cli", lambda: None)


@pytest.fixture(autouse=True)
def _signed_in_test_home(tmp_path, monkeypatch):
    """Give every test a signed-in Codex test home and a CLI test folder of its own.

    :param tmp_path: the test's temporary directory
    :param monkeypatch: pytest's monkeypatch
    """
    monkeypatch.setenv(run.CODEX_HOME_VARIABLE, str(tmp_path / "codex-home"))
    monkeypatch.setenv(run.CLI_CONFIG_VARIABLE, str(tmp_path / "reef-cli"))
    monkeypatch.setattr(
        run, "codex_login_status", lambda: (0, "Logged in using ChatGPT")
    )


def _capture_subprocess(monkeypatch) -> list[dict]:
    """Replace ``subprocess.run`` in the runner and note each call.

    :param monkeypatch: pytest's monkeypatch
    :returns: the list the calls are noted in; each has ``command`` and ``env``
    """
    calls: list[dict] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Note the call and print nothing.

        :param command: the command
        :param kwargs: the other arguments
        :returns: an empty, successful result
        """
        calls.append({"command": command, "env": kwargs.get("env")})
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(run.subprocess, "run", fake)
    return calls


def test_every_codex_command_runs_in_the_test_home(tmp_path, monkeypatch):
    """Both targets, resumed turns and the account check get the test home."""
    monkeypatch.delenv(run.CODEX_HOME_VARIABLE)
    default = tmp_path / "default-home"
    monkeypatch.setattr(run, "DEFAULT_CODEX_HOME", default)
    monkeypatch.setenv("CODEX_HOME", str(Path.home() / ".codex"))
    monkeypatch.setattr(run, "cli_token", lambda: "bench-token-0")
    before = dict(os.environ)
    calls = _capture_subprocess(monkeypatch)
    scenario = {"id": "x", "messages": ["a", "b"], "connector": False}
    for server in (run.local_server("ab" * 32), run.PRODUCTION_SERVER):
        run._run_codex(
            run.codex_command(server, tools=None, session_id=None), "a", tmp_path
        )
        run._run_codex(
            run.codex_command(server, tools=None, session_id="t-1"), "b", tmp_path
        )
        run.play(scenario, server, run.PROBE_TOOLS)
    assert len(calls) == 6
    expected = str(default)
    for call in calls:
        assert call["command"][:2] == ["codex", "exec"]
        assert call["env"]["CODEX_HOME"] == expected
        assert call["env"]["PATH"] == os.environ["PATH"]
    assert dict(os.environ) == before


def test_codex_home_can_be_overridden(tmp_path, monkeypatch):
    """``REEF_CONVERSATIONS_CODEX_HOME`` replaces the default folder."""
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("REEF_CONVERSATIONS_CODEX_HOME", str(other))
    calls = _capture_subprocess(monkeypatch)
    run._run_codex(["codex", "exec", "-"], "hi", tmp_path)
    assert run.codex_home() == other
    assert calls[0]["env"]["CODEX_HOME"] == str(other)
    monkeypatch.delenv("REEF_CONVERSATIONS_CODEX_HOME")
    assert run.codex_home() == run.DEFAULT_CODEX_HOME
    assert run.DEFAULT_CODEX_HOME == Path.home() / ".reef-conversations" / "codex"


def test_preparing_the_home_makes_it_private(tmp_path):
    """The folder is made when missing, readable by its owner only."""
    home = run.prepare_codex_home()
    assert home.is_dir() and home.stat().st_mode & 0o777 == 0o700
    assert list(home.iterdir()) == []


async def test_not_signed_in_stops_before_any_reset(tmp_path, monkeypatch):
    """No sign-in in the test home: stop, say the command, touch nothing."""
    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(run, "codex_login_status", lambda: (1, "Not logged in"))
    monkeypatch.delenv(run.CODEX_HOME_VARIABLE)
    monkeypatch.setattr(run, "DEFAULT_CODEX_HOME", tmp_path / "default-home")
    recorder = _Recorder(_probe(_index_call(read_fixture().persona)))
    monkeypatch.setattr(run, "play", recorder)
    target = _FakeTarget()
    with pytest.raises(run.RunError) as caught:
        await run.run(target, ["dentist-moved"], None)
    message = str(caught.value)
    assert "not signed in to Codex" in message
    assert "CODEX_HOME=~/.reef-conversations/codex codex login" in message
    assert "does not affect your own Codex" in message
    assert target.resets == 0 and recorder.played == []


async def test_status_exit_code_decides_signed_in(tmp_path, monkeypatch):
    """A zero exit that says "Not logged in" still counts as not signed in."""
    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(run, "codex_login_status", lambda: (0, "Not logged in"))
    with pytest.raises(run.RunError, match="not signed in"):
        await run.run(_FakeTarget(), ["dentist-moved"], None)


async def test_agents_file_in_the_test_home_stops_the_run(tmp_path, monkeypatch):
    """An ``AGENTS.md`` in the test home would reach every conversation."""
    home = Path(os.environ[run.CODEX_HOME_VARIABLE])
    home.mkdir()
    (home / "AGENTS.md").write_text("Always answer in rhyme.", encoding="utf-8")
    target = _FakeTarget()
    with pytest.raises(run.RunError, match="AGENTS.md"):
        await run.run(target, ["dentist-moved"], None)
    assert target.resets == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
async def test_unwritable_home_stops_the_run(tmp_path, monkeypatch):
    """A read-only test home stops the run and names the folder."""
    home = tmp_path / "readonly"
    home.mkdir()
    home.chmod(0o500)
    monkeypatch.setenv(run.CODEX_HOME_VARIABLE, str(home))
    target = _FakeTarget()
    try:
        with pytest.raises(run.RunError) as caught:
            await run.run(target, ["dentist-moved"], None)
    finally:
        home.chmod(0o700)
    assert str(home) in str(caught.value)
    assert "read and write access" in str(caught.value)
    assert target.resets == 0


async def test_report_names_the_codex_home(tmp_path, monkeypatch):
    """The report's meta says which Codex home the run used."""
    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    recorder = _Recorder(_probe(_index_call(read_fixture().persona)))
    monkeypatch.setattr(run, "play", recorder)
    await run.run(_FakeTarget(), ["dentist-moved"], None)
    meta = _report(tmp_path / "reports")["meta"]
    assert meta["codex_home"] == os.environ[run.CODEX_HOME_VARIABLE]


@pytest.mark.parametrize("inside", ["", "sub/dir"])
async def test_own_codex_home_is_refused(tmp_path, monkeypatch, inside):
    """``~/.codex``, or a folder inside it, is never the test home."""
    fake_home = tmp_path / "person"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    own = fake_home / ".codex"
    link = tmp_path / "link"
    link.symlink_to(fake_home)
    # A symlink and a trailing slash must not get around the refusal.
    monkeypatch.setenv(run.CODEX_HOME_VARIABLE, f"{link}/.codex/{inside}/")
    target = _FakeTarget()
    with pytest.raises(run.RunError, match="separate folder"):
        await run.run(target, ["dentist-moved"], None)
    assert not own.exists()
    assert target.resets == 0


def test_relative_override_becomes_absolute(tmp_path, monkeypatch):
    """A relative folder is resolved once, so ``codex`` sees the same one."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(run.CODEX_HOME_VARIABLE, "rel/home")
    assert run.codex_home() == (tmp_path / "rel" / "home").resolve()
    assert run.codex_env()["CODEX_HOME"] == str((tmp_path / "rel" / "home").resolve())


def test_sign_out_advice_names_the_actual_home(tmp_path, monkeypatch):
    """The advice shows the default folder literally, else a quoted path."""
    monkeypatch.delenv(run.CLI_CONFIG_VARIABLE)
    default = run.PRODUCTION_SERVER.advice()
    assert "REEF_CONFIG_DIR=~/.reef-conversations/reef-cli reef logout" in default
    cli_folder = tmp_path / "my cli"
    monkeypatch.setenv(run.CLI_CONFIG_VARIABLE, str(cli_folder))
    advice = run.PRODUCTION_SERVER.advice()
    quoted = shlex.quote(str(cli_folder.resolve()))
    assert f"REEF_CONFIG_DIR={quoted} reef logout" in advice
    assert f"REEF_CONFIG_DIR={quoted} reef login" in advice
    other = tmp_path / "my home"
    monkeypatch.setenv(run.CODEX_HOME_VARIABLE, str(other))
    monkeypatch.setattr(run, "codex_login_status", lambda: (1, "Not logged in"))
    with pytest.raises(run.RunError) as caught:
        run.check_codex_home()
    assert f"CODEX_HOME={shlex.quote(str(other.resolve()))} codex login" in str(
        caught.value
    )


async def test_checks_run_in_order(tmp_path, monkeypatch):
    """Own home, writable, no AGENTS.md, signed in, and only then the first reset."""
    events: list[str] = []
    status = (0, "Logged in using ChatGPT")

    def signed_in() -> tuple[int, str]:
        """Note the sign-in check and answer with the current status.

        :returns: the status
        """
        events.append("signed-in")
        return status

    class Tracing(_FakeTarget):
        """A target that notes its resets."""

        async def reset(self) -> run.Services:
            """Note the reset, then reset.

            :returns: the services
            """
            events.append("reset")
            return await super().reset()

    async def attempt(match: str | None) -> None:
        """Run once; expect the refusal in ``match``, or a full run.

        :param match: text the refusal must hold, or None when the run passes
        """
        events.clear()
        if match is None:
            await run.run(Tracing(), ["dentist-moved"], None)
            return
        with pytest.raises(run.RunError, match=match):
            await run.run(Tracing(), ["dentist-moved"], None)

    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(run, "codex_login_status", signed_in)
    monkeypatch.setattr(
        run, "play", _Recorder(_probe(_index_call(read_fixture().persona)))
    )
    person = tmp_path / "person"
    (person / ".codex").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: person)
    test_home = tmp_path / "test-home"

    # 1. Your own home comes first, though it holds an AGENTS.md too.
    (person / ".codex" / "AGENTS.md").write_text("x", encoding="utf-8")
    monkeypatch.setenv(run.CODEX_HOME_VARIABLE, str(person / ".codex"))
    await attempt("separate folder")
    assert events == []

    # 2. Then writability, though the home holds an AGENTS.md as well.
    monkeypatch.setenv(run.CODEX_HOME_VARIABLE, str(test_home))
    test_home.mkdir()
    (test_home / "AGENTS.md").write_text("x", encoding="utf-8")
    if os.geteuid() != 0:
        test_home.chmod(0o500)
        try:
            await attempt("read and write access")
        finally:
            test_home.chmod(0o700)
        assert events == []

    # 3. Then the AGENTS.md, before any call to Codex.
    await attempt("AGENTS.md")
    assert events == []

    # 4. Then the sign-in, before the first reset.
    (test_home / "AGENTS.md").unlink()
    status = (1, "Not logged in")
    await attempt("not signed in")
    assert events == ["signed-in"]

    # 5. When every check passes, the first reset comes after them.
    status = (0, "Logged in using ChatGPT")
    await attempt(None)
    assert events[:2] == ["signed-in", "reset"]


@pytest.mark.parametrize("around", [".", ".."])
async def test_a_folder_around_your_codex_home_is_refused(
    tmp_path, monkeypatch, around
):
    """Your home folder, or one above it, would put Codex's files among yours."""
    person = tmp_path / "people" / "person"
    person.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: person)
    monkeypatch.setenv(run.CODEX_HOME_VARIABLE, str(person / around))
    target = _FakeTarget()
    with pytest.raises(run.RunError, match="separate folder"):
        await run.run(target, ["dentist-moved"], None)
    assert list(person.iterdir()) == []
    assert target.resets == 0


#: A made-up production token. It has no dot, so the report's own redaction
#: cannot hide it: if it reaches a report, a test sees it.
TOKEN = "bench-token-7f3a9c41d2"


def test_reef_bench_is_reef_and_reef_test_is_not():
    """Calls to ``reef_bench`` are Reef's; the retired ``reef_test`` is another tool."""
    calls = [
        {
            "type": "item.completed",
            "item": {
                "id": str(number),
                "type": "mcp_tool_call",
                "server": server,
                "tool": "recall",
                "status": "completed",
            },
        }
        for number, server in enumerate(["reef_bench", "reef_test"])
    ]
    session = _probe(*calls)
    assert [(call.server, call.tool) for call in session.tool_calls] == [
        ("reef_bench", "recall")
    ]
    assert session.other_tools == ["reef_test.recall"]


def test_production_codex_command_names_reef_bench():
    """Codex reaches production as ``reef_bench``, with the token from a variable."""
    probe = run.codex_command(
        run.PRODUCTION_SERVER, tools=run.PROBE_TOOLS, session_id=None
    )
    connector = run.codex_command(
        run.PRODUCTION_SERVER, tools=run.CONNECTOR_TOOLS, session_id="t-1"
    )
    for command in (probe, connector):
        assert 'mcp_servers.reef_bench.url="https://reefwith.me/mcp"' in command
        assert (
            'mcp_servers.reef_bench.bearer_token_env_var="REEF_BENCH_TOKEN"' in command
        )
        assert 'mcp_servers.reef_bench.default_tools_approval_mode="approve"' in command
        assert not any("reef_test" in part for part in command)
    assert 'mcp_servers.reef_bench.enabled_tools=["load_index"]' in probe
    assert 'mcp_servers.reef_bench.enabled_tools=["search", "fetch"]' in connector
    assert run.PRODUCTION_SERVER.bearer and not run.local_server("ab" * 32).bearer


def test_codex_cannot_start_a_turn_without_reef():
    """Reef is a required server: Codex waits for its tools, or stops.

    A server that is not required gets about one second at the start of a
    turn. After that the model gets no Reef tool and answers from what it can
    see. ``startup_readiness`` did not change that, so it is not passed.
    """
    for server in (run.PRODUCTION_SERVER, run.local_server("ab" * 32)):
        first = run.codex_command(server, tools=run.PROBE_TOOLS, session_id=None)
        again = run.codex_command(server, tools=None, session_id="t-1")
        for command in (first, again):
            setting = f"mcp_servers.{server.name}.required=true"
            assert command[command.index(setting) - 1] == "-c"
            assert not any("startup_readiness" in part for part in command)


def test_codex_gets_the_token_only_when_given(monkeypatch):
    """A token goes into the child's environment; none from the shell ever does."""
    monkeypatch.setenv(run.TOKEN_VARIABLE, "stale-from-the-shell")
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "the-operators-own-token")
    for env in (run.codex_env(), run.codex_env(TOKEN)):
        assert "REEF_ACCESS_TOKEN" not in env
    assert run.TOKEN_VARIABLE not in run.codex_env()
    assert run.codex_env(TOKEN)[run.TOKEN_VARIABLE] == TOKEN
    assert os.environ[run.TOKEN_VARIABLE] == "stale-from-the-shell"
    assert os.environ["REEF_ACCESS_TOKEN"] == "the-operators-own-token"


class _FakeCommands:
    """Stands in for ``subprocess.run``: the Reef command line tool and Codex.

    Notes every call with its command and environment. ``reef token`` prints
    :data:`TOKEN`, or fails when asked to. A reset and ``reef open`` print
    what the runner expects. ``codex exec`` prints the sample record, as
    ``reef_bench``; when asked, a resumed turn fails with the token in its
    error output, as a careless Codex might print it.
    """

    def __init__(
        self,
        *,
        token_status: int = 0,
        token_out: str = TOKEN + "\n",
        token_err: str = "",
        resume_fails: bool = False,
        reset_error: str | None = None,
    ) -> None:
        """Choose how ``reef token``, a reset and a resumed turn answer.

        :param token_status: the exit status of ``reef token``
        :param token_out: what ``reef token`` prints
        :param token_err: what ``reef token`` prints as an error
        :param resume_fails: make every resumed turn fail
        :param reset_error: the code every reset is refused with, or None
        """
        self.calls: list[dict] = []
        self.token_status = token_status
        self.token_out = token_out
        self.token_err = token_err
        self.resume_fails = resume_fails
        self.reset_error = reset_error
        self.sample = SAMPLE.read_text(encoding="utf-8").replace(
            "reef_local", "reef_bench"
        )

    def kind(self, command: list[str]) -> str:
        """Name a command: ``warmup``, ``token``, ``reset``, ``open``, ``codex`` or ``resume``.

        :param command: the command
        :returns: its kind
        """
        if command[:2] == ["codex", "exec"]:
            return "resume" if "resume" in command else "codex"
        if command[-1] == "--help":
            return "warmup"
        assert command[: len(run._CLI)] == run._CLI, command
        return {"token": "token", "reset-test-account": "reset", "open": "open"}[
            command[len(run._CLI)]
        ]

    def __call__(
        self, command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess:
        """Note the call and answer it.

        :param command: the command
        :param kwargs: the other arguments of ``subprocess.run``
        :returns: the answer
        """
        assert TOKEN not in os.environ.values()
        kind = self.kind(command)
        self.calls.append({"kind": kind, "command": command, "env": kwargs.get("env")})
        if kind == "token":
            return subprocess.CompletedProcess(
                command, self.token_status, self.token_out, self.token_err
            )
        if kind == "reset" and self.reset_error is not None:
            refused = json.dumps({"error": self.reset_error})
            return subprocess.CompletedProcess(command, 1, refused, "")
        if kind == "reset":
            body = {
                "reset": True,
                "services": {"files": True, "file_text": True},
                "fixture_version": "1",
                "reef_version": "9.9",
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(body), "")
        if kind == "open":
            opened = {"structured_content": {"result": []}}
            return subprocess.CompletedProcess(command, 0, json.dumps(opened), "")
        if kind == "resume" and self.resume_fails:
            error = f"error: MCP request failed: Authorization: Bearer {TOKEN}\n"
            return subprocess.CompletedProcess(command, 1, "", error)
        return subprocess.CompletedProcess(command, 0, self.sample, "")


async def test_production_run_gives_the_token_to_codex_only(
    tmp_path, monkeypatch, capsys
):
    """A fresh token before every ``codex exec``, in its environment and nowhere else."""
    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "the-operators-own-token")
    folder = Path(os.environ[run.CLI_CONFIG_VARIABLE])
    before = dict(os.environ)
    fake = _FakeCommands(resume_fails=True)
    monkeypatch.setattr(run.subprocess, "run", fake)
    await run.run(run.ProductionTarget(), ["dentist-moved", "forget-confirmed"], None)
    kinds = [call["kind"] for call in fake.calls]
    # The sign-in check, the first reset; then a token before each Codex turn.
    assert kinds[:2] == ["token", "reset"]
    assert kinds.count("codex") == 3 and kinds.count("resume") == 1
    for index, kind in enumerate(kinds):
        if kind in ("codex", "resume"):
            assert kinds[index - 1] == "token"
    assert kinds.count("token") == 1 + 4
    for call in fake.calls:
        assert TOKEN not in " ".join(call["command"])
        assert "REEF_ACCESS_TOKEN" not in call["env"]
        if call["kind"] in ("codex", "resume"):
            assert call["env"][run.TOKEN_VARIABLE] == TOKEN
            assert call["env"]["CODEX_HOME"] == os.environ[run.CODEX_HOME_VARIABLE]
        else:
            assert call["env"]["REEF_CONFIG_DIR"] == str(folder)
            assert run.TOKEN_VARIABLE not in call["env"]
            assert "REEF_ACCESS_TOKEN" not in call["env"]
    assert dict(os.environ) == before
    printed = capsys.readouterr()
    assert TOKEN not in printed.out + printed.err
    reports = list((tmp_path / "reports").iterdir())
    assert len(reports) == 2
    for path in reports:
        text = path.read_text(encoding="utf-8")
        assert TOKEN not in text
        assert "the-operators-own-token" not in text
    report = _report(tmp_path / "reports")
    forget = next(s for s in report["scenarios"] if s["id"] == "forget-confirmed")
    assert forget["verdict"] == "invalid"


def test_every_reef_command_gets_the_test_cli_folder(tmp_path, monkeypatch):
    """Resets, reads and tokens all use the test folder, set only for the child."""
    fake = _FakeCommands()
    monkeypatch.setattr(run.subprocess, "run", fake)
    monkeypatch.setenv("REEF_CONFIG_DIR", str(Path.home() / ".config" / "reef"))
    before = dict(os.environ)
    target = run.ProductionTarget()
    asyncio.run(target.reset())
    asyncio.run(target.read(["personal/a.md"]))
    assert run.cli_token() == TOKEN
    assert dict(os.environ) == before
    other = tmp_path / "elsewhere"
    monkeypatch.setenv(run.CLI_CONFIG_VARIABLE, str(other))
    run.cli_token()
    monkeypatch.delenv(run.CLI_CONFIG_VARIABLE)
    run.cli_token()
    folders = [call["env"]["REEF_CONFIG_DIR"] for call in fake.calls]
    first = str(tmp_path / "reef-cli")
    default = str(Path.home().resolve() / ".reef-conversations" / "reef-cli")
    assert folders == [first, first, first, str(other), default]
    assert [call["kind"] for call in fake.calls] == [
        "reset",
        "open",
        "token",
        "token",
        "token",
    ]
    assert run.DEFAULT_CLI_CONFIG == Path(default)


@pytest.mark.parametrize(
    ("folder", "why"),
    [
        (".config/reef", "the same folder"),
        (".config/reef/sub/dir", "a folder inside it"),
        (".config", "a folder around it"),
        (".", "your home folder"),
    ],
)
async def test_own_cli_folder_is_refused(tmp_path, monkeypatch, folder, why):
    """``~/.config/reef``, or a folder in or around it, is never the test folder.

    It is refused before anything else: before the Codex home, any sign-in
    check and any reset.

    :param folder: the test folder, from the home folder
    :param why: what the folder is to ``~/.config/reef``
    """
    person = tmp_path / "person"
    person.mkdir()
    monkeypatch.setattr(Path, "home", lambda: person)
    link = tmp_path / "link"
    link.symlink_to(person)
    # A symlink and a trailing slash must not get around the refusal.
    monkeypatch.setenv(run.CLI_CONFIG_VARIABLE, f"{link}/{folder}/")
    events: list[str] = []
    monkeypatch.setattr(
        run, "codex_login_status", lambda: events.append("codex") or (0, "")
    )
    monkeypatch.setattr(run, "cli_token", lambda: events.append("token") or TOKEN)
    target = _FakeTarget(server=run.PRODUCTION_SERVER)
    with pytest.raises(run.RunError, match="separate folder") as caught:
        await run.run(target, ["dentist-moved"], None)
    assert "~/.config/reef" in str(caught.value)
    assert run.CLI_CONFIG_VARIABLE in str(caught.value)
    assert events == [] and target.resets == 0
    assert not Path(os.environ[run.CODEX_HOME_VARIABLE]).exists()
    assert list(person.iterdir()) == []


@pytest.mark.parametrize(
    ("status", "out", "err", "signed_out"),
    [
        (1, "", '{"error": "not_signed_in"}\n', True),
        (1, "", '{"error": "server_error"}\n', False),
        (0, "\n", "", False),
        (1, "", f"reef: the server said {TOKEN}\n", False),
        (1, "", f'{{"error": "not_signed_in", "detail": "{TOKEN}"}}\n', True),
    ],
    ids=[
        "not-signed-in",
        "server-error",
        "printed-nothing",
        "failed-loudly",
        "signed-out-loudly",
    ],
)
async def test_failing_token_stops_before_any_reset(
    tmp_path, monkeypatch, status, out, err, signed_out
):
    """No token from the command line tool: stop, say what to do, touch nothing.

    Only ``not_signed_in`` asks for a new sign-in; anything else asks to try
    again. What the tool printed is never shown.

    :param status: the exit status of ``reef token``
    :param out: what it prints
    :param err: what it prints as an error
    :param signed_out: whether the run must ask for a new sign-in
    """
    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    monkeypatch.delenv(run.CLI_CONFIG_VARIABLE)
    monkeypatch.setattr(run, "DEFAULT_CLI_CONFIG", tmp_path / "default-cli")
    fake = _FakeCommands(token_status=status, token_out=out, token_err=err)
    monkeypatch.setattr(run.subprocess, "run", fake)
    recorder = _Recorder(_probe(_index_call(read_fixture().persona)))
    monkeypatch.setattr(run, "play", recorder)
    target = _FakeTarget(server=run.PRODUCTION_SERVER)
    with pytest.raises(run.RunError) as caught:
        await run.run(target, ["dentist-moved"], None)
    message = str(caught.value)
    login = "REEF_CONFIG_DIR=~/.reef-conversations/reef-cli reef login"
    if signed_out:
        assert login in message
        assert "private window" in message and "test account" in message
    else:
        assert login not in message
        assert "server or network problem" in message and "Try again" in message
    assert TOKEN not in message and "the server said" not in message
    assert "server_error" not in message
    assert [call["kind"] for call in fake.calls] == ["token"]
    assert target.resets == 0 and recorder.played == []


async def test_local_run_needs_no_cli_sign_in(tmp_path, monkeypatch):
    """The local target never asks the command line tool for anything."""
    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    monkeypatch.setenv(run.CLI_CONFIG_VARIABLE, str(Path.home() / ".config" / "reef"))
    fake = _FakeCommands()
    monkeypatch.setattr(run.subprocess, "run", fake)
    monkeypatch.setattr(
        run, "play", _Recorder(_probe(_index_call(read_fixture().persona)))
    )
    await run.run(_FakeTarget(), ["dentist-moved"], None)
    assert fake.calls == []


@pytest.mark.parametrize("code", ["not_a_test_account", "unauthenticated"])
async def test_reset_refused_for_another_sign_in_says_how_to_sign_in(
    tmp_path, monkeypatch, code
):
    """A reset refused for the sign-in stops the run before Codex, with the fix.

    :param code: the refusal's code
    """
    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    monkeypatch.delenv(run.CLI_CONFIG_VARIABLE)
    monkeypatch.setattr(run, "DEFAULT_CLI_CONFIG", tmp_path / "default-cli")
    fake = _FakeCommands(reset_error=code)
    monkeypatch.setattr(run.subprocess, "run", fake)
    with pytest.raises(run.RunError) as caught:
        await run.run(run.ProductionTarget(), ["dentist-moved"], None)
    message = str(caught.value)
    assert f"the reset failed: {code}" in message
    assert "REEF_CONFIG_DIR=~/.reef-conversations/reef-cli reef login" in message
    assert "private window" in message and "test account" in message
    assert [call["kind"] for call in fake.calls] == ["token", "reset"]


def test_other_reset_refusals_do_not_ask_for_a_sign_in(monkeypatch):
    """A refusal that is not about the sign-in names only its code."""
    monkeypatch.setattr(
        run.subprocess, "run", _FakeCommands(reset_error="shared_with_others")
    )
    with pytest.raises(run.RunError) as caught:
        asyncio.run(run.ProductionTarget().reset())
    assert str(caught.value) == "the reset failed: shared_with_others"


@pytest.mark.parametrize(
    "folder", ["reef", "reef/sub", ".", ".."], ids=["same", "inside", "xdg", "above"]
)
async def test_xdg_cli_folder_is_refused(tmp_path, monkeypatch, folder):
    """With ``XDG_CONFIG_HOME`` set, its ``reef`` folder is yours too.

    :param folder: the test folder, from ``$XDG_CONFIG_HOME``
    """
    xdg = tmp_path / "people" / "xdg"
    xdg.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(xdg)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(link))
    monkeypatch.setenv(run.CLI_CONFIG_VARIABLE, f"{xdg}/{folder}/")
    monkeypatch.setattr(run, "cli_token", lambda: pytest.fail("token asked for"))
    target = _FakeTarget(server=run.PRODUCTION_SERVER)
    with pytest.raises(run.RunError, match="separate folder") as caught:
        await run.run(target, ["dentist-moved"], None)
    assert str((xdg / "reef").resolve()) in str(caught.value)
    assert target.resets == 0 and list(xdg.iterdir()) == []
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert run.check_cli_config_dir() == (xdg / folder).resolve()


async def test_warm_up_happens_once_before_the_first_token(tmp_path, monkeypatch):
    """The environment is built once per run, before the first ``reef token``.

    ``uv`` may take minutes to build it on a fresh worktree; that must not
    be taken for a server problem. It gets a long limit and no network.
    """
    monkeypatch.setattr(run, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(run, "warm_up_cli", _REAL_WARM_UP)
    fake = _FakeCommands()
    monkeypatch.setattr(run.subprocess, "run", fake)
    await run.run(run.ProductionTarget(), ["dentist-moved", "bench-record"], None)
    kinds = [call["kind"] for call in fake.calls]
    assert kinds.count("warmup") == 1
    assert kinds[:2] == ["warmup", "token"]
    assert kinds.count("token") > 1


def test_warm_up_is_generous_and_does_not_stop_the_run(monkeypatch):
    """A long limit, the same project as ``reef``; a failed warm-up is not fatal."""
    seen: list[dict] = []

    def slow(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        """Note the call, then time out.

        :param command: the command
        :param kwargs: the other arguments
        :raises subprocess.TimeoutExpired: always
        """
        seen.append({"command": command, **kwargs})
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(run.subprocess, "run", slow)
    _REAL_WARM_UP()
    assert seen[0]["command"] == [*run._CLI[:-2], "--help"]
    assert seen[0]["timeout"] >= 600 > run.TOKEN_TIMEOUT
    assert seen[0]["env"]["REEF_CONFIG_DIR"]


async def test_report_says_when_the_run_finished(tmp_path, monkeypatch):
    """A completed run has a ``finished`` time, not ``None``."""
    monkeypatch.setattr(run, "REPORTS", tmp_path)
    monkeypatch.setattr(
        run, "play", _Recorder(_probe(_index_call(read_fixture().persona)))
    )
    await run.run(_FakeTarget(), ["dentist-moved"], None)
    meta = _report(tmp_path)["meta"]
    assert meta["finished"] >= meta["started"]
    assert "stopped" not in meta


async def test_stopped_run_also_has_a_finished_time(tmp_path, monkeypatch):
    """A run that stopped says when, next to why."""
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
    with pytest.raises(RuntimeError, match="boom"):
        await run.run(_FakeTarget(), ["dentist-moved"], None)
    meta = _report(tmp_path)["meta"]
    assert "boom" in meta["stopped"] and meta["finished"] >= meta["started"]


def _deadlift() -> dict:
    """Load the unprompted-memory scenario from the scenarios file.

    :returns: the ``deadlift-tomorrow`` scenario
    """
    return next(s for s in load(SCENARIOS) if s["id"] == "deadlift-tomorrow")


def test_generic_deadlift_answer_does_not_pass():
    """Advice without the memory fails, even with "terug" or "rug" in it."""
    answer = "Ja hoor, kom rustig terug in vorm en let op je rug en onderrug."
    scored = _score(_deadlift(), _session("load_index", answer=answer))
    assert scored.verdict != "pass"


def test_memory_deadlift_answer_passes_without_the_weight():
    """Any detail from the memory is enough, here the physiotherapist's name."""
    answer = "Rustig aan: Joris Peeters zei dat je beter core-oefeningen doet."
    scored = _score(_deadlift(), _session("load_index", "recall", answer=answer))
    assert scored.verdict == "pass", scored.reasons


def test_alternative_tools_are_met_by_either():
    """A list in ``tools_required`` needs one of its tools, not all."""
    scenario = _scenario(tools_required=["load_index", ["open", "recall"]])
    for tool in ("open", "recall"):
        scored = _score(scenario, _session("load_index", tool, answer=RIGHT_ANSWER))
        assert scored.verdict == "pass", (tool, scored.reasons)


def test_unmet_alternatives_give_one_combined_reason():
    """None of the alternatives used: one reason names them all."""
    scenario = _scenario(tools_required=["load_index", ["open", "recall"]])
    scored = _score(scenario, _session("load_index", answer=RIGHT_ANSWER))
    assert scored.verdict == "fail"
    assert scored.reasons == ["none of open, recall was used successfully"]


def test_failed_call_to_the_only_used_alternative_does_not_count():
    """An alternative whose only call failed is not used."""
    scenario = _scenario(tools_required=["load_index", ["open", "recall"]])
    session = Session(
        "t", [_call("load_index"), _call("open", ok=False)], [], RIGHT_ANSWER
    )
    scored = _score(scenario, session)
    assert scored.verdict == "fail"
    assert "none of open, recall was used successfully" in scored.reasons


def test_coverage_counts_alternatives():
    """Every tool named inside a list of alternatives is covered."""
    scenarios = [{"tools_required": ["load_index", ["open", "recall"]]}]
    assert coverage(scenarios) == {"load_index", "open", "recall"}


def test_training_log_passes_with_recall_alone_when_quoted_in_full():
    """The production case: only recall, and every line quoted word for word."""
    scenario = next(s for s in load(SCENARIOS) if s["id"] == "open-training-log")
    answer = (
        "Uit logs/training.md:\n"
        "- Deadlift 140 x 3, 3 sets. Rug voelde stijf.\n"
        "- Roeien 72,5 x 8.\n"
        "- Deadlift overgeslagen vanwege de rug.\n"
        "- Schouderdrukken 42,5 x 8.\n"
        "- Optrekken +12,5 kg x 6, 3 sets. Roeien 75 x 8: record.\n"
        "- Squat 115 x 5, 5 sets: record. Deadlift 130 x 5, 3 sets, rustig."
    )
    scored = _score(scenario, _session("load_index", "recall", answer=answer))
    assert scored.verdict == "pass", scored.reasons
