"""Public questions use the made-up account's facts and links."""

import re
from collections import Counter
from pathlib import Path

import pytest
from test_measure_recall import measure_recall

from reef.links import parse_target
from reef.testaccount.fixture import read_fixture


@pytest.fixture
def recall_sets():
    """Read the public questions in the measurement script's own format."""
    path = Path(__file__).parent / "fixtures" / "recall_sets.json"
    return measure_recall.parse_questions(path.read_text())


def _questions(items):
    """Yield each question, including the turns inside sessions."""
    return [question for item in items for question in item.get("session", [item])]


def test_public_sets_have_enough_questions_and_two_turn_sessions(recall_sets):
    """Each mechanism has its required set, with separate subject changes."""
    counts = Counter(q.get("set") for q in _questions(recall_sets))
    assert counts["linked"] >= 10
    for name, minimum in (("follow_up", 10), ("change_of_subject", 5)):
        sessions = [
            item["session"]
            for item in recall_sets
            if "session" in item and item["session"][-1].get("set") == name
        ]
        assert len(sessions) >= minimum
        assert all(len(session) == 2 for session in sessions)
        assert all("set" not in session[0] for session in sessions)


def test_every_expected_and_via_reference_exists(recall_sets):
    """No score can depend on an absent fixture engram."""
    refs = {f"personal/{engram.path}" for engram in read_fixture().engrams}
    for question in _questions(recall_sets):
        assert set(question["expected"]) <= refs
        if "via" in question:
            assert question["via"] in refs


def test_linked_questions_follow_links_written_in_the_fixture(recall_sets):
    """The bridge is a real wiki link in either direction, not a label."""
    edges = set()
    for engram in read_fixture().engrams:
        for target in re.findall(r"\[\[([^\]]+)\]\]", engram.body):
            alias, path = parse_target(target)
            edges.add((f"personal/{engram.path}", f"{alias or 'personal'}/{path}"))
    for question in _questions(recall_sets):
        if question.get("set") == "linked":
            via = question["via"]
            assert all(
                (via, ref) in edges or (ref, via) in edges
                for ref in question["expected"]
            )
            assert via not in question["expected"]


def test_follow_ups_need_context_and_subject_changes_expect_something_else(recall_sets):
    """A follow-up omits title words; a subject change expects a new engram."""
    titles = {f"personal/{e.path}": e.title for e in read_fixture().engrams}
    stop = {
        "de",
        "het",
        "een",
        "en",
        "van",
        "voor",
        "naar",
        "in",
        "op",
        "with",
        "on",
        "the",
    }
    for item in recall_sets:
        if "session" not in item:
            continue
        first, second = item["session"]
        if second["set"] == "follow_up":
            assert second["expected"] == first["expected"]
            words = set(re.findall(r"\w+", second["text"].lower())) - stop
            for ref in second["expected"]:
                assert not words & (set(re.findall(r"\w+", titles[ref].lower())) - stop)
        else:
            assert second["set"] == "change_of_subject"
            assert set(first["expected"]).isdisjoint(second["expected"])
