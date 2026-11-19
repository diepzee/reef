"""Find: ``brief``, and the log helpers it reads logs with.

Like the other find tests, these manage their own transactions.
"""

import pytest

from reef.methods import REGISTRY, MethodError, find, logs
from reef.models import Engram
from tests.test_method_find import _save, caller_for


def _entries(first: int, last: int) -> str:
    """Write log entries, one per day, as ``###`` sections.

    :param first: the first day
    :param last: the last day
    :returns: the markdown
    """
    return "".join(
        f"### 2026-01-{day:02d}\nClimbed on day {day}.\n\n"
        for day in range(first, last + 1)
    )


def test_brief_is_a_method():
    assert "brief" in REGISTRY


async def test_brief_gives_plan_and_recent_entries(household):
    me = caller_for(household["wouter"])
    await _save(me, "personal", "climbing-plan.md", "The climbing plan: hang twice.")
    await _save(
        me,
        "personal",
        "climbing-log.md",
        "# Climbing log\n\nIntro.\n\n## Entries\n\n" + _entries(1, 5),
        tags=["log"],
    )
    result = await find.brief(me, "next session", "climbing", entries=2)
    items = {item["engram"]: item for item in result["engrams"]}
    assert set(items) == {"climbing-plan.md", "climbing-log.md"}
    plan = items["climbing-plan.md"]
    assert plan["body"] == "The climbing plan: hang twice."
    assert plan["parts"] == ["climbing-plan.md"]
    log = items["climbing-log.md"]
    assert "body" not in log
    assert log["space"] == "personal"
    assert log["parts"] == ["climbing-log.md"]
    assert [entry.splitlines()[0] for entry in log["entries"]] == [
        "### 2026-01-04",
        "### 2026-01-05",
    ]


async def test_brief_stitches_entries_across_parts(household):
    me = caller_for(household["wouter"])
    await _save(
        me,
        "personal",
        "climbing-log.md",
        "# Climbing\n\n" + _entries(1, 3),
        tags=["log"],
    )
    await _save(
        me,
        "personal",
        "climbing-log-2.md",
        "Continues [climbing-log](climbing-log.md).\n\n" + _entries(4, 4),
        tags=["log"],
    )
    result = await find.brief(me, "next session", "climbing", entries=3)
    [item] = result["engrams"]
    assert item["engram"] == "climbing-log.md"
    assert item["parts"] == ["climbing-log.md", "climbing-log-2.md"]
    assert [entry.splitlines()[0] for entry in item["entries"]] == [
        "### 2026-01-02",
        "### 2026-01-03",
        "### 2026-01-04",
    ]


async def test_brief_stays_within_budget(household):
    me = caller_for(household["wouter"])
    for number in range(5):
        await _save(me, "personal", f"budget-{number}.md", "budget " * 3_000)
    result = await find.brief(me, "money", "budget")
    sizes = [len(item["body"]) for item in result["engrams"]]
    assert sizes == [8_000, 8_000, 8_000, 6_000]


async def test_brief_cuts_a_log_from_its_oldest_entries(household, monkeypatch):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(find, "BRIEF_BUDGET", 100)
    await _save(
        me, "personal", "climbing-log.md", "# Log\n\n" + _entries(1, 9), tags=["log"]
    )
    [item] = (await find.brief(me, "", "climbing", entries=9))["engrams"]
    assert sum(len(entry) for entry in item["entries"]) <= 100
    assert item["entries"][-1].startswith("### 2026-01-09")
    assert item["entries"][0] != _entries(1, 1).strip()


async def test_brief_without_matches_is_empty(household):
    me = caller_for(household["wouter"])
    assert await find.brief(me, "", "zeppelin") == {"engrams": []}


async def test_brief_never_shows_what_the_caller_may_not_read(household):
    partner = caller_for(household["partner"])
    await _save(partner, "personal", "secret.md", "The zeppelin plan.")
    me = caller_for(household["wouter"])
    assert await find.brief(me, "", "zeppelin") == {"engrams": []}


async def test_brief_clamps_entries(household):
    me = caller_for(household["wouter"])
    await _save(
        me, "personal", "climbing-log.md", "# Log\n\n" + _entries(1, 9), tags=["log"]
    )
    [item] = (await find.brief(me, "", "climbing", entries=0))["engrams"]
    assert len(item["entries"]) == 1


async def test_brief_refuses_an_unknown_space(household):
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await find.brief(me, "", "climbing", space="nowhere")
    assert refused.value.code == "unknown_space"
    assert "nowhere" not in refused.value.message
    assert "@" not in refused.value.message


async def test_recall_refuses_an_unknown_space(household):
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await find.recall(me, "", "climbing", space="nowhere")
    assert refused.value.code == "unknown_space"


async def test_series_orders_parts_numerically(household):
    me = caller_for(household["wouter"])
    for path in ("a-log.md", "a-log-2.md", "a-log-10.md", "a-log-3.md"):
        await _save(me, "personal", path, _entries(1, 1), tags=["log"])
    await _save(me, "personal", "a-log-4.md", _entries(1, 1))
    for start in ("a-log-10.md", "a-log.md", "a-log-3.md"):
        parts = await logs.series(me, "personal", start)
        assert [part.path for part in parts] == [
            "a-log.md",
            "a-log-2.md",
            "a-log-3.md",
            "a-log-10.md",
        ]


async def test_series_is_empty_for_what_is_not_a_log(household):
    me = caller_for(household["wouter"])
    await _save(me, "personal", "note.md", "Not a log.")
    assert await logs.series(me, "personal", "note.md") == []
    assert await logs.series(me, "personal", "missing.md") == []


async def test_last_entries_use_level_two_without_level_three(household):
    me = caller_for(household["wouter"])
    body = "# Log\n\nIntro.\n\n## One\nx\n\n## Two\ny\n"
    await _save(me, "personal", "l.md", body, tags=["log"])
    parts = await logs.series(me, "personal", "l.md")
    assert logs.last_entries(parts, 5) == ["## One\nx", "## Two\ny"]
    assert logs.last_entries(parts, 0) == []


def test_last_entries_skip_a_tilde_line_inside_a_backtick_fence():
    """A fence closes only on its own marker, as when sections are cut."""
    body = "### Day 1\n```\n~~~\n### not an entry\n```\n\n### Day 2\nx\n"
    entries = logs.last_entries([Engram(body=body, tags=["log"])], 5)
    assert [entry.splitlines()[0] for entry in entries] == ["### Day 1", "### Day 2"]
