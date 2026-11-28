"""The recall measurement: numbers only, and no trace in the search log.

These tests manage their own transactions: the script opens its own, and has
to see committed rows.
"""

import json
import sys
from pathlib import Path

import pytest
from test_search_files import _file

from reef.access import Principal
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import save_engram

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import measure_recall

MARKER = "ZEBRAQUILT"


async def test_measuring_prints_numbers_only_and_logs_no_search(
    household, seed, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings.search, "log", True)
    monkeypatch.setattr(settings.embedding, "url", "")
    person = household["wouter"]
    me = Principal(person_id=person.id, email=person.email)
    async with transaction_scope():
        await save_engram(
            me, "personal", "boiler.md", "The Vaillant boiler.", message="x"
        )
        await _file(
            me,
            household["w_personal"],
            "polis.pdf",
            "A scan.",
            ["Dear neighbours", "The xylophone recital starts at eight."],
        )
    questions = [
        {
            "id": "q1",
            "text": f"Vaillant {MARKER}",
            "expected": ["personal/boiler.md"],
        },
        {
            "id": "q2",
            "text": f"xylophone {MARKER}",
            "expected": [f"personal/file/{_key()}"],
            "answer": "starts at eight",
        },
        {"id": "q3", "text": f"nothing {MARKER}", "expected": ["personal/none.md"]},
    ]
    out = await measure_recall.run(
        ["--email", person.email, "--file-text", "on"], json.dumps(questions)
    )
    report = json.loads(out)
    assert report["engrams"] == {"asked": 2, "found_at_5": 1}
    assert report["files"] == {"asked": 1, "found_at_5": 1, "answer_in_passage": 1}
    assert report["median_ms"] >= 0
    for private in (MARKER, "xylophone", "polis", "boiler", "starts at eight"):
        assert private not in out
    assert await seed.fetchval("SELECT count(*) FROM search_log") == 0


async def test_with_file_text_off_a_page_word_finds_no_file(household, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings.embedding, "url", "")
    person = household["wouter"]
    me = Principal(person_id=person.id, email=person.email)
    async with transaction_scope():
        await _file(me, household["w_personal"], "polis.pdf", "A scan.", ["xylophone"])
    questions = [
        {"id": "q", "text": "xylophone", "expected": [f"personal/file/{_key()}"]}
    ]
    out = await measure_recall.run(
        ["--email", person.email, "--file-text", "off"], json.dumps(questions)
    )
    assert json.loads(out)["files"]["found_at_5"] == 0


def _key() -> str:
    """Return the object key `_file` gives its file.

    :returns: the key
    """
    return "files/polis.pdf"


def test_a_question_without_an_expected_ref_is_refused():
    with pytest.raises(SystemExit):
        measure_recall.parse_questions('[{"id": "q", "text": "t", "expected": []}]')


async def test_sessions_pass_only_their_earlier_engram_ids_and_count_sets(
    household, monkeypatch
):
    """History is newest first, excludes files, and ends with its session."""
    person = household["wouter"]
    me = Principal(person_id=person.id, email=person.email)
    async with transaction_scope():
        a = await save_engram(me, "personal", "a.md", "one", message="x")
        b = await save_engram(me, "personal", "b.md", "two", message="x")
    seen = []

    async def search(principal, query, **kwargs):
        """Return made hits while observing each call's independent history."""
        seen.append(kwargs.get("earlier"))
        path = "a.md" if query == "one" else "b.md"
        return [
            {"kind": "page", "cove": "personal", "path": path, "passage": "answer"},
            {
                "kind": "file",
                "cove": "personal",
                "key": "files/other",
                "passage": "file",
            },
        ]

    monkeypatch.setattr(measure_recall, "search_engrams", search)
    first = {"text": "one", "expected": ["personal/a.md"]}
    second = {"text": "two", "expected": ["personal/b.md"], "set": "follow_up"}
    third = {
        "text": "three",
        "expected": ["personal/missing.md"],
        "set": "change_of_subject",
    }
    questions = measure_recall.parse_questions(
        json.dumps([{"session": [first, second, third]}, first])
    )
    report = await measure_recall.measure(me, questions)
    assert seen == [[], [[a.id]], [[b.id], [a.id]], []]
    assert report["engrams"] == {"asked": 4, "found_at_5": 3}
    assert report["sets"]["follow_up"]["asked"] == 1
    assert report["sets"]["follow_up"]["found_at_5"] == 1
    assert report["sets"]["change_of_subject"]["found_at_5"] == 0
    assert all(tally["median_ms"] >= 0 for tally in report["sets"].values())


async def test_plain_questions_keep_old_counts_and_add_empty_sets(
    household, monkeypatch
):
    """An old question file needs no new labels or session wrapper."""
    person = household["wouter"]
    me = Principal(person_id=person.id, email=person.email)

    async def nothing(*args, **kwargs):
        """Leave a plain question unanswered."""
        return []

    monkeypatch.setattr(measure_recall, "search_engrams", nothing)
    report = await measure_recall.measure(
        me, [{"text": "question", "expected": ["personal/missing.md"]}]
    )
    assert report == {
        "engrams": {"asked": 1, "found_at_5": 0},
        "files": {"asked": 0, "found_at_5": 0, "answer_in_passage": 0},
        "median_ms": report["median_ms"],
        "sets": {},
    }


@pytest.mark.parametrize("fails", [False, True])
async def test_switch_flags_apply_only_during_this_measurement(
    household, monkeypatch, fails
):
    """Process overrides are restored even when measuring raises."""
    settings = get_settings().search
    before = (
        settings.priming,
        settings.spreading,
        settings.priming_lift,
        settings.spreading_lift,
    )
    observed = []

    async def measure(*args):
        """Observe the process settings before returning or failing."""
        observed.append(
            (
                settings.priming,
                settings.spreading,
                settings.priming_lift,
                settings.spreading_lift,
            )
        )
        if fails:
            raise RuntimeError("measurement failed")
        return {"asked": 0}

    monkeypatch.setattr(measure_recall, "measure", measure)
    args = [
        "--email",
        household["wouter"].email,
        "--file-text",
        "off",
        "--priming",
        "on",
        "--spreading",
        "off",
        "--priming-lift",
        "0.5",
        "--spreading-lift",
        "1.0",
    ]
    if fails:
        with pytest.raises(RuntimeError, match="measurement failed"):
            await measure_recall.run(args, "[]")
    else:
        assert json.loads(await measure_recall.run(args, "[]")) == {"asked": 0}
    assert observed == [(True, False, 0.5, 1.0)]
    assert (
        settings.priming,
        settings.spreading,
        settings.priming_lift,
        settings.spreading_lift,
    ) == before


@pytest.mark.parametrize(
    "item",
    [
        {"session": []},
        {"session": "bad"},
        {"session": [{"text": "missing expected"}]},
        {"text": "q", "expected": ["personal/a.md"], "set": 5},
    ],
)
def test_bad_session_and_set_shapes_are_refused_without_echoing_text(item):
    """Input mistakes give a short structural error, without private input."""
    with pytest.raises(SystemExit) as error:
        measure_recall.parse_questions(json.dumps([item]))
    assert "missing expected" not in str(error.value)


async def test_a_negative_lift_flag_is_refused_before_measurement():
    """Flag assignment must not bypass the setting's non-negative bound."""
    with pytest.raises(SystemExit):
        await measure_recall.run(
            [
                "--email",
                "nobody@example.test",
                "--file-text",
                "off",
                "--priming-lift",
                "-1",
            ],
            "[]",
        )
