"""Public questions use the made-up account's facts and links."""

import re
from collections import Counter
from pathlib import Path

import pytest
from test_measure_recall import measure_recall

from reef.access import Principal
from reef.config import get_settings
from reef.links import parse_target
from reef.search import search_engrams
from reef.testaccount.fixture import load_fixture, read_fixture


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


async def test_public_sets_need_their_stated_context_by_words(
    recall_sets, household, monkeypatch
):
    """Words alone leave room for context and still find a changed subject."""
    person = household["wouter"]
    me = Principal(person_id=person.id, email=person.email)
    settings = get_settings()
    monkeypatch.setattr(settings.search, "priming", False)
    monkeypatch.setattr(settings.search, "spreading", False)
    monkeypatch.setattr(settings.reading, "enabled", False)
    await load_fixture(me, read_fixture(), None)
    failures = []
    for item in recall_sets:
        question = item["session"][-1] if "session" in item else item
        hits = await search_engrams(
            me, question["text"], limit=50, semantic=False, rerank=False, files=False
        )
        refs = [f"{hit['cove']}/{hit['path']}" for hit in hits]
        expected = question["expected"]
        if question["set"] == "follow_up":
            valid = not set(expected) & set(refs[:5])
        elif question["set"] == "linked":
            via = question["via"]
            valid = (
                via in refs
                and all(
                    ref not in refs or refs.index(via) < refs.index(ref)
                    for ref in expected
                )
                and not set(expected) & set(refs[:1])
            )
        else:
            valid = set(expected) <= set(refs[:5])
        if not valid:
            failures.append((question["id"], refs[:5]))
    assert not failures, "\n".join(f"{name}: {refs}" for name, refs in failures)
