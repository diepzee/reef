"""The retrieval benchmark: its scoring, its guard, and a slice that must pass.

The slice is two dozen LongMemEval questions with their evidence and a few
other sessions, kept in the repository (MIT licence, see the file's
``source``). It is small and easy on purpose. It does not measure quality;
it fails the build when a change to search makes it find fewer of the pages
it used to find.

These tests manage their own transactions rather than requesting ``tx``:
the harness opens its own, and has to see committed rows.
"""

import os
from pathlib import Path

import evalkit
import pytest

from reef.access import Principal
from reef.db import transaction_scope
from reef.pages import list_pages

SLICE = Path(__file__).parent / "fixtures" / "longmemeval_slice.json"

#: What search reaches on the slice today, less a little room. A change that
#: lowers a score below its floor has made search worse and must say why.
#: A change that raises a score should raise its floor.
FLOORS = {
    "lexical": {"recall_any": 0.75, "mrr": 0.60},
    "lexical-any": {"recall_any": 0.75, "mrr": 0.60},
}


def _record(question_id: str, evidence: list[str], kind: str = "multi-session") -> dict:
    """Build one benchmark question with three sessions.

    :param question_id: the question's id
    :param evidence: ids of the sessions holding the answer
    :param kind: the question type
    :returns: a record shaped like the benchmark file's
    """
    ids = ["s1", "s2", "s3"]
    return {
        "question_id": question_id,
        "question_type": kind,
        "question": "Which boiler do we have?",
        "answer": "A Vaillant",
        "answer_session_ids": evidence,
        "haystack_session_ids": ids,
        "haystack_dates": ["2023/05/20 (Sat) 02:21"] * 3,
        "haystack_sessions": [
            [{"role": "user", "content": f"text of {sid} " + "x" * n}]
            for n, sid in enumerate(ids)
        ],
    }


# --- the guard -----------------------------------------------------------------


def test_a_local_database_is_accepted(monkeypatch):
    for name in list(os.environ):
        if name.startswith("RAILWAY_"):
            monkeypatch.delenv(name)
    evalkit.refuse_unless_local(
        "postgresql://reef:reef@localhost:5433/reef",
        "postgresql://postgres:postgres@127.0.0.1:5433/reef",
    )


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://reef:x@postgres.railway.internal:5432/railway",
        "postgresql://reef:x@db.example.com/reef",
        "postgresql://reef:x@localhost.example.com/reef",
        "postgresql:///reef",
    ],
)
def test_any_other_database_is_refused(dsn, monkeypatch):
    for name in list(os.environ):
        if name.startswith("RAILWAY_"):
            monkeypatch.delenv(name)
    with pytest.raises(evalkit.NotLocal):
        evalkit.refuse_unless_local("postgresql://reef:reef@localhost/reef", dsn)


def test_a_railway_environment_is_refused_whatever_the_address(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(evalkit.NotLocal):
        evalkit.refuse_unless_local("postgresql://reef:reef@localhost/reef")


async def test_a_database_somebody_signed_in_to_is_refused(graph):
    """A tunnel to production has a local address. Its people give it away."""
    from conftest import seed_dsn

    await graph.person("real@example.test", "Real", subject="user_01ABC")
    with pytest.raises(evalkit.NotLocal):
        await evalkit.ensure_eval_person(seed_dsn())


async def test_the_eval_person_is_created_once_with_a_personal_cove(graph):
    from conftest import seed_dsn

    await graph.person("dev@example.test", "Dev")
    first = await evalkit.ensure_eval_person(seed_dsn())
    second = await evalkit.ensure_eval_person(seed_dsn())
    assert first.person_id == second.person_id
    async with transaction_scope():
        assert await list_pages(first, "personal") is not None


# --- the benchmark, as pages ---------------------------------------------------


def test_a_session_becomes_a_page_reef_accepts():
    from reef.pages import normalize_path

    page = evalkit.session_page(
        "sharegpt_yywfIrx_0",
        "2023/05/20 (Sat) 02:21",
        [
            {"role": "user", "content": "Which boiler?"},
            {"role": "assistant", "content": "A Vaillant."},
        ],
    )
    assert page.path == normalize_path(page.path)
    assert page.path.startswith("sessions/sharegpt-yywfirx-0-")
    assert page.title == "Conversation on 2023/05/20"
    assert page.body == "**User:** Which boiler?\n\n**Assistant:** A Vaillant."


def test_one_session_id_with_two_texts_becomes_two_pages():
    one = evalkit.session_page("s1", "2023/05/20", [{"role": "user", "content": "a"}])
    same = evalkit.session_page("s1", "2023/06/01", [{"role": "user", "content": "a"}])
    other = evalkit.session_page("s1", "2023/05/20", [{"role": "user", "content": "b"}])
    assert one.path == same.path
    assert one.path != other.path


def test_a_question_expects_the_pages_of_its_evidence_sessions():
    eval_set = evalkit.convert([_record("q1", ["s2"]), _record("q2", ["s1", "s3"])])
    assert len(eval_set.pages) == 3
    by_id = {q.id: q for q in eval_set.questions}
    assert len(by_id["q1"].expected) == 1
    assert by_id["q1"].expected[0].startswith("sessions/s2-")
    assert len(by_id["q2"].expected) == 2


def test_a_slice_keeps_the_evidence_and_the_shortest_other_sessions():
    eval_set = evalkit.convert([_record("q1", ["s3"])], distractors=1)
    assert sorted(p.path.split("-")[0] for p in eval_set.pages) == [
        "sessions/s1",
        "sessions/s3",
    ]


def test_abstention_questions_are_left_out():
    records = [_record("q1", ["s1"]), _record("q2_abs", [])]
    assert [r["question_id"] for r in evalkit.choose(records, None)] == ["q1"]


def test_choosing_is_repeatable_and_keeps_each_type():
    records = [
        _record(f"q{n:02}", ["s1"], kind="temporal" if n % 4 else "update")
        for n in range(40)
    ]
    first = evalkit.choose(records, 8)
    assert first == evalkit.choose(list(reversed(records)), 8)
    assert len(first) == 8
    assert {r["question_type"] for r in first} == {"temporal", "update"}


def test_a_set_survives_being_written_and_read():
    eval_set = evalkit.convert([_record("q1", ["s2"])])
    assert evalkit.EvalSet.from_json(eval_set.to_json()) == eval_set


# --- scoring -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("returned", "any_hit", "all_hit", "reciprocal_rank"),
    [
        (["a", "b", "x"], True, True, 1.0),
        (["x", "a", "y"], True, False, 0.5),
        (["x", "y", "z", "a"], False, False, 0.25),
        (["x", "y"], False, False, 0.0),
        ([], False, False, 0.0),
    ],
)
def test_judging_one_question(returned, any_hit, all_hit, reciprocal_rank):
    assert evalkit.judge(["a", "b"], returned, k=3) == (
        any_hit,
        all_hit,
        reciprocal_rank,
    )


def test_a_report_counts_by_type_and_lists_what_was_missed():
    questions = [
        evalkit.EvalQuestion("q1", "update", "one?", "1", ("a",)),
        evalkit.EvalQuestion("q2", "update", "two?", "2", ("b",)),
        evalkit.EvalQuestion("q3", "temporal", "three?", "3", ("c",)),
    ]
    report = evalkit.score(
        "lexical", questions, {"q1": ["a"], "q2": ["x", "b"], "q3": []}, k=1
    ).to_dict()
    assert report["overall"] == {
        "questions": 3,
        "recall_any": 0.3333,
        "recall_all": 0.3333,
        "mrr": 0.5,
    }
    assert report["by_kind"]["update"]["recall_any"] == 0.5
    assert [f["id"] for f in report["failures"]] == ["q2", "q3"]
    assert report["failures"][0]["returned"] == ["x"]


# --- the slice, through the real write path and the real search ----------------


@pytest.fixture
def slice_set() -> evalkit.EvalSet:
    """Read the committed slice.

    :returns: its questions and pages
    """
    return evalkit.EvalSet.from_json(SLICE.read_text())


@pytest.fixture
async def eval_principal(graph) -> Principal:
    """Create a person with a personal cove to hold the slice.

    :param graph: the topology builders
    :returns: the person, as a principal
    """
    person = await graph.person(evalkit.EVAL_EMAIL, "Eval")
    await graph.personal_cove(person, slug="eval")
    return Principal(person_id=person.id, email=person.email)


def test_the_slice_is_whole(slice_set):
    paths = {page.path for page in slice_set.pages}
    assert len(slice_set.questions) == 24
    assert all(q.expected and set(q.expected) <= paths for q in slice_set.questions)
    assert "MIT" in slice_set.source


async def test_seeding_twice_writes_each_page_once(eval_principal, slice_set):
    pages = slice_set.pages[:5]
    assert await evalkit.seed_pages(eval_principal, pages) == 5
    assert await evalkit.seed_pages(eval_principal, pages) == 0
    async with transaction_scope():
        stored = await list_pages(eval_principal, "personal")
    assert {page.path for page in stored} == {page.path for page in pages}


async def test_search_still_finds_what_it_found(eval_principal, slice_set):
    await evalkit.seed_pages(eval_principal, slice_set.pages)
    for mode, floors in FLOORS.items():
        report = await evalkit.run(eval_principal, slice_set.questions, mode)
        scores = report.overall.summary()
        for name, floor in floors.items():
            assert scores[name] >= floor, (
                f"{mode}: {name} fell to {scores[name]}, below its floor of {floor}"
            )
