"""Score a test conversation, and write the report.

A scenario says which Reef tools the assistant must use or must not use,
which facts and engrams its answer must hold, and what the memory must hold
afterwards. :func:`score` turns what Codex did into one verdict:

* ``skipped``: the target lacks something the scenario needs.
* ``invalid``: there is no session, Codex failed, the assistant used a
  tool outside Reef, or Reef refused the bearer sign-in. The run says
  nothing about Reef.
* ``fail``: a required tool is missing, a forbidden tool was used, or the
  memory afterwards is not as it must be.
* ``pass``: the tools are right and every fact and engram is in the answer.
* ``review``: a fact or engram is not in the answer, or a call failed to a
  tool named in ``review_failed_tools``. A person reads the conversation.

For ``pass``, ``fail`` and ``review``, every failed call to a Reef tool is
listed in the reasons. A pass after a retry still shows the call that failed.
A scenario can name ``review_failed_tools`` to ask for review even when a
later call succeeds.

Reports never hold a staged token, a share nonce or a signed link.
"""

import json
import re
import tomllib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from events import Session

Verdict = Literal["pass", "fail", "review", "skipped", "invalid"]

#: What a scenario may need from the target before it can be played.
NEEDS = frozenset({"files", "file_text"})

#: A sealed staged token: two long URL-safe base64 parts joined by a dot.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")

#: A UUID, which is what a share nonce looks like.
_NONCE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)

#: A link that carries a signature: it works for whoever holds it.
_SIGNED_URL = re.compile(
    r"https?://[^\s<>\"')]*(?:X-Amz-|[?&]Signature=|[?&]token=)[^\s<>\"')]*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Scored:
    """The verdict on one scenario.

    :param id: the scenario's id
    :param verdict: the verdict
    :param reasons: what led to it, in plain words
    :param tools: the Reef tools the assistant called, in order
    :param seconds: how long the conversation took
    """

    id: str
    verdict: Verdict
    reasons: list[str]
    tools: list[str]
    seconds: float


def load(path: Path | str) -> list[dict]:
    """Read the scenarios, with every optional field filled in.

    :param path: the TOML file
    :returns: the scenarios, in file order
    """
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    defaults: dict = {
        "tools_required": [],
        "tools_forbidden": [],
        "review_failed_tools": [],
        "facts": [],
        "engrams": [],
        "needs": [],
        "connector": False,
        "after": [],
        "answer_must_not": [],
        "review": False,
    }
    return [defaults | scenario for scenario in raw["scenario"]]


def coverage(scenarios: list[dict]) -> set[str]:
    """Name the tools the scenarios require.

    :param scenarios: the scenarios
    :returns: every tool named in a ``tools_required``, also inside a list of
        alternatives
    """
    return {
        tool
        for scenario in scenarios
        for entry in scenario["tools_required"]
        for tool in ([entry] if isinstance(entry, str) else entry)
    }


def normalize(text: str) -> str:
    """Make text comparable: lower case, no emphasis marks, one space.

    :param text: the text
    :returns: the text, ready for a substring search
    """
    cleaned = re.sub(r"[*`]", "", text.replace(" ", " "))
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _alternatives(fact: str | list[str]) -> list[str]:
    """List the ways a fact may be written.

    :param fact: one text, or a list of ways to write it
    :returns: the ways, normalized
    """
    return [normalize(fact)] if isinstance(fact, str) else [normalize(f) for f in fact]


def _holds(text: str, fact: str | list[str]) -> bool:
    """Say whether a text holds a fact in at least one of its spellings.

    :param text: the normalized text
    :param fact: one text, or a list of ways to write it
    :returns: True when found
    """
    return any(way in text for way in _alternatives(fact))


def _label(fact: str | list[str]) -> str:
    """Name a fact for a reason: its first spelling.

    :param fact: one text, or a list of ways to write it
    :returns: the text to show
    """
    return fact if isinstance(fact, str) else fact[0]


def _check_after(checks: list[dict], after: dict[str, str | dict | None]) -> list[str]:
    """Compare the memory afterwards with what a scenario wants.

    :param checks: the scenario's ``after`` checks
    :param after: bodies and optional replacement notes, by ref; None when absent
    :returns: one reason for every check that failed
    """
    reasons: list[str] = []
    for check in checks:
        ref = check["ref"]
        if ref not in after:
            reasons.append(f"{ref} was not read afterwards")
            continue
        entry = after[ref]
        body = entry.get("body") if isinstance(entry, dict) else entry
        absent = check.get("absent", [])
        if absent is True:
            if body is not None:
                reasons.append(f"{ref} still exists")
            continue
        if body is None:
            reasons.append(f"{ref} does not exist")
            continue
        replacements = entry.get("replaced_by", []) if isinstance(entry, dict) else []
        refs = {f"{item['space']}/{item['path']}" for item in replacements}
        reasons += [
            f"{ref} is not replaced by {replacement}"
            for replacement in check.get("replaced_by", [])
            if replacement not in refs
        ]
        text = normalize(body)
        reasons += [
            f"{ref} lacks {_label(fact)!r}"
            for fact in check.get("contains", [])
            if not _holds(text, fact)
        ]
        reasons += [
            f"{ref} still holds {old!r}" for old in absent if normalize(old) in text
        ]
    return reasons


def score(
    scenario: dict,
    session: Session | None,
    after: dict[str, str | dict | None],
    *,
    files: bool,
    file_text: bool,
    seconds: float,
    bearer: bool = False,
) -> Scored:
    """Give a scenario its verdict.

    A scenario that needs what the target lacks is skipped before anything
    else: it was never played, so it has no session.

    :param scenario: the scenario, as :func:`load` returns it
    :param session: what Codex did, or None when it never ran
    :param after: the engram bodies read after a write scenario, by ref
    :param files: whether the target stores files
    :param file_text: whether the target reads the text in files
    :param seconds: how long the conversation took
    :param bearer: whether Codex used a bearer token for this scenario
    :returns: the verdict, with the reasons
    """
    id_ = scenario["id"]
    needs = scenario.get("needs", [])
    lacking = [
        need
        for need in needs
        if (need == "files" and not files) or (need == "file_text" and not file_text)
    ]
    if lacking:
        return Scored(
            id_, "skipped", [f"the target lacks {', '.join(lacking)}"], [], seconds
        )
    if session is None:
        return Scored(id_, "invalid", ["no conversation was played"], [], seconds)
    tools = [call.tool for call in session.tool_calls]
    if bearer and any(call.auth_required for call in session.tool_calls):
        return Scored(
            id_,
            "invalid",
            ["the sign-in ran out while the model was answering; run it again"],
            tools,
            seconds,
        )
    if session.error:
        return Scored(
            id_, "invalid", [f"Codex failed: {session.error}"], tools, seconds
        )
    if session.other_tools:
        used = ", ".join(sorted(set(session.other_tools)))
        return Scored(
            id_, "invalid", [f"used tools outside Reef: {used}"], tools, seconds
        )
    worked = {call.tool for call in session.tool_calls if call.ok}
    broken = [
        f"the call to {call.tool} failed" for call in session.tool_calls if not call.ok
    ]
    failures = []
    for entry in scenario["tools_required"]:
        # A list is a set of alternatives: one of them is enough.
        names = [entry] if isinstance(entry, str) else list(entry)
        if worked.isdisjoint(names):
            failures.append(
                f"required tool {names[0]} was not used successfully"
                if len(names) == 1
                else f"none of {', '.join(names)} was used successfully"
            )
    failures += [
        f"forbidden tool {t} was used"
        for t in scenario["tools_forbidden"]
        if t in tools
    ]
    if len(scenario.get("messages", [])) > 1 and any(
        call.turn == 0 and call.arguments.get("confirm") for call in session.tool_calls
    ):
        failures.append("confirmed before the person said yes")
    failures += _check_after(scenario.get("after", []), after)
    failures += [
        f"the answer matches {pattern!r}"
        for pattern in scenario.get("answer_must_not", [])
        if re.search(pattern, session.answer)
    ]
    if failures:
        return Scored(id_, "fail", failures + broken, tools, seconds)
    answer = normalize(session.answer)
    doubts = [
        f"fact not found: {_label(fact)}"
        for fact in scenario["facts"]
        if not _holds(answer, fact)
    ]
    doubts += [
        f"engram not named: {path}"
        for path in scenario["engrams"]
        if normalize(path) not in answer
    ]
    if scenario.get("review"):
        doubts.append("a person must read the answer")
    review_failed = any(
        not call.ok and call.tool in scenario.get("review_failed_tools", [])
        for call in session.tool_calls
    )
    if doubts or review_failed:
        return Scored(id_, "review", doubts + broken, tools, seconds)
    return Scored(id_, "pass", broken, tools, seconds)


def redact(text: str) -> str:
    """Remove what must not reach a report: tokens, nonces and signed links.

    :param text: the text
    :returns: the text without them
    """
    text = _SIGNED_URL.sub("[signed link removed]", text)
    text = _TOKEN.sub("[token removed]", text)
    return _NONCE.sub("[nonce removed]", text)


def _redacted(value: object) -> object:
    """Redact every string inside a JSON-like value.

    :param value: a string, or a list or dict holding values
    :returns: the same shape, with every string redacted
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    return value


def write_report(
    directory: Path,
    stem: str,
    meta: dict,
    scored: list[Scored],
    answers: dict[str, str],
) -> list[Path]:
    """Write a report as JSON and as Markdown, with nothing private in it.

    :param directory: where reports go; made when missing
    :param stem: the file name without its extension
    :param meta: facts about the run, such as the target
    :param scored: the verdicts, in the order they were played
    :param answers: each scenario's last answer, by id
    :returns: the JSON file, then the Markdown file
    """
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    totals = dict(Counter(item.verdict for item in scored))
    scenarios = [
        {**asdict(item), "answer": answers.get(item.id, "")} for item in scored
    ]
    report = _redacted({"meta": meta, "totals": totals, "scenarios": scenarios})
    assert isinstance(report, dict)
    json_path = directory / f"{stem}.json"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path = directory / f"{stem}.md"
    md_path.write_text(_markdown(report), encoding="utf-8")
    return [json_path, md_path]


def _markdown(report: dict) -> str:
    """Lay a report out for a person to read.

    :param report: the redacted report
    :returns: the Markdown text
    """
    meta = ", ".join(f"{key}: {value}" for key, value in report["meta"].items())
    totals = ", ".join(
        f"{count} {verdict}" for verdict, count in report["totals"].items()
    )
    lines = [
        "# Test conversations",
        "",
        meta,
        "",
        f"Result: {totals or 'nothing played'}",
        "",
    ]
    lines += ["| Scenario | Verdict | Seconds | Tools |", "| --- | --- | --- | --- |"]
    for item in report["scenarios"]:
        tools = " ".join(item["tools"])
        lines.append(
            f"| {item['id']} | {item['verdict']} | {item['seconds']:.0f} | {tools} |"
        )
    for item in report["scenarios"]:
        if item["verdict"] == "skipped" or not item["reasons"]:
            continue
        lines += ["", f"## {item['id']}: {item['verdict']}", ""]
        lines += [f"- {reason}" for reason in item["reasons"]]
        if item["answer"]:
            lines += ["", *(f"> {line}" for line in item["answer"].splitlines())]
    return "\n".join(lines) + "\n"
