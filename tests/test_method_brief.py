"""Find: ``brief``, and the log helpers it reads logs with.

Like the other find tests, these manage their own transactions.
"""

from unittest.mock import AsyncMock

import pytest
from conftest import attach_file, caller_for, save_as

from reef.access import arm
from reef.attachments import get_attachment
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.methods import REGISTRY, MethodError, find, logs
from reef.models import Engram


@pytest.mark.parametrize("visible", [True, False])
async def test_brief_names_only_readable_replacements(household, visible) -> None:
    """Old bodies carry the replacement note without revealing private engrams."""
    from reef.models import LinkKind
    from reef.relations import Relations

    writer = caller_for(household["wouter"])
    reader = writer if visible else caller_for(household["partner"])
    await save_as(writer, "household", "old.md", "contract")
    await save_as(
        writer,
        "personal",
        "new.md",
        "renewal",
        channel="person",
        relations=Relations({LinkKind.SUPERSEDES: ("household:old.md",)}),
    )
    [item] = (await find.brief(reader, "", "contract", space="household"))["engrams"]
    assert item["body"] == "contract"
    if visible:
        assert item["replaced_by"] == [
            {"space": "personal", "path": "new.md", "title": "new"}
        ]
    else:
        assert "replaced_by" not in item


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
    await save_as(me, "personal", "climbing-plan.md", "The climbing plan: hang twice.")
    await save_as(
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
    await save_as(
        me,
        "personal",
        "climbing-log.md",
        "# Climbing\n\n" + _entries(1, 3),
        tags=["log"],
    )
    await save_as(
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


async def test_brief_records_each_log_part_once(household, monkeypatch, seed):
    """Hits in several parts of one log record one read per part."""
    me = caller_for(household["wouter"])
    paths = ["climbing-log.md", "climbing-log-2.md"]
    for day, path in enumerate(paths, 1):
        await save_as(me, "personal", path, _entries(day, day), tags=["log"])
    hits = [{"kind": "page", "cove": "personal", "path": path} for path in paths]
    monkeypatch.setattr(find, "search_engrams", AsyncMock(return_value=hits[::-1]))
    [item] = (await find.brief(me, "", "climbing"))["engrams"]
    assert item["parts"] == paths
    rows = await seed.fetch(
        "SELECT engram_id, count(*) AS reads FROM engram_access "
        "WHERE via = 'read' GROUP BY engram_id"
    )
    assert len(rows) == 2
    assert [row["reads"] for row in rows] == [1, 1]


async def test_brief_fetches_a_non_log_only_once(household, monkeypatch, seed):
    """Repeated hits need one body fetch and one usage row for a plain engram."""
    me = caller_for(household["wouter"])
    path = "climbing-plan.md"
    await save_as(me, "personal", path, "Climb twice.")
    hit = {"kind": "page", "cove": "personal", "path": path}
    monkeypatch.setattr(find, "search_engrams", AsyncMock(return_value=[hit, hit]))
    queries: list[str] = []
    run_querystring = DB.run_querystring

    async def track_reads(engine, querystring, *args, **kwargs):
        """Count body fetches while executing each query normally.

        :param engine: the database engine
        :param querystring: the query to execute
        :param args: further positional arguments
        :param kwargs: further keyword arguments
        :returns: the database result
        """
        query, parameters = querystring.compile_string(engine_type=DB.engine_type)
        if (
            query.startswith("SELECT")
            and 'FROM "engrams"' in query
            and path in parameters
        ):
            queries.append(query)
        return await run_querystring(querystring, *args, **kwargs)

    monkeypatch.setattr(type(DB), "run_querystring", track_reads)
    [item] = (await find.brief(me, "", "climbing"))["engrams"]
    assert item["body"] == "Climb twice."
    assert len(queries) == 1
    assert (
        await seed.fetchval("SELECT count(*) FROM engram_access WHERE via = 'read'")
        == 1
    )


async def test_brief_keeps_overlapping_log_series(household, monkeypatch, seed):
    """A nested part hit must not suppress entries from its parent's series."""
    me = caller_for(household["wouter"])
    for day, path in enumerate(["x.md", "x-2.md", "x-2-2.md"], 1):
        await save_as(me, "personal", path, _entries(day, day), tags=["log"])
    hits = [
        {"kind": "page", "cove": "personal", "path": path}
        for path in ["x-2-2.md", "x-2.md"]
    ]
    monkeypatch.setattr(find, "search_engrams", AsyncMock(return_value=hits))
    items = (await find.brief(me, "", "climbing"))["engrams"]
    assert [item["parts"] for item in items] == [
        ["x-2.md", "x-2-2.md"],
        ["x.md", "x-2.md"],
    ]
    text = "\n".join(entry for item in items for entry in item["entries"])
    assert all(f"Climbed on day {day}." in text for day in range(1, 4))
    rows = await seed.fetch(
        "SELECT engram_id, count(*) AS reads FROM engram_access "
        "WHERE via = 'read' GROUP BY engram_id"
    )
    assert len(rows) == 3
    assert all(row["reads"] == 1 for row in rows)


async def test_brief_starts_from_a_hit_only_in_part_two(household):
    """A match in part two brings back the base and entries from both parts."""
    me = caller_for(household["wouter"])
    await save_as(
        me,
        "personal",
        "training-log.md",
        _entries(1, 3),
        title="Training",
        tags=["log"],
    )
    latest = "### 2026-01-04\nPractised dynos."
    await save_as(
        me,
        "personal",
        "training-log-2.md",
        latest,
        title="Training continued",
        tags=["log"],
    )
    hits = await find.search_engrams(me.principal, "dynos")
    assert [hit["path"] for hit in hits] == ["training-log-2.md"]

    [item] = (await find.brief(me, "prepare a session", "dynos", entries=3))["engrams"]

    assert item["engram"] == "training-log.md"
    assert item["title"] == "Training"
    assert item["space"] == "personal"
    assert item["parts"] == ["training-log.md", "training-log-2.md"]
    assert item["entries"] == [_entries(2, 2).strip(), _entries(3, 3).strip(), latest]


async def test_brief_stays_within_budget(household):
    me = caller_for(household["wouter"])
    for number in range(5):
        await save_as(me, "personal", f"budget-{number}.md", "budget " * 3_000)
    result = await find.brief(me, "money", "budget")
    sizes = [len(item["body"]) for item in result["engrams"]]
    assert sizes == [8_000, 8_000, 8_000, 6_000]


async def test_brief_cuts_a_log_from_its_oldest_entries(household, monkeypatch):
    me = caller_for(household["wouter"])
    monkeypatch.setattr(find, "BRIEF_BUDGET", 100)
    await save_as(
        me, "personal", "climbing-log.md", "# Log\n\n" + _entries(1, 9), tags=["log"]
    )
    [item] = (await find.brief(me, "", "climbing", entries=9))["engrams"]
    assert sum(len(entry) for entry in item["entries"]) <= 100
    assert item["entries"][-1].startswith("### 2026-01-09")
    assert item["entries"][0] != _entries(1, 1).strip()


async def _climbing_files(me, count: int) -> None:
    """Store files that match "climbing" better than any engram does.

    Each has the word in its description and on its one page, and is newer
    than the engrams, which wins a tie.

    :param me: who stores them
    :param count: how many
    """
    for _ in range(count):
        key = await attach_file(me, "personal", "Climbing climbing climbing.")
        async with transaction_scope():
            await arm(me.principal)
            file = await get_attachment(me.principal, "personal", key)
            assert file is not None
            await Engram.raw(
                "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
                "VALUES (gen_random_uuid(), {}, 1, 'Climbing climbing.', 'pdf-text')",
                file.id,
            )


async def test_brief_keeps_five_engrams_when_files_match_better(household, monkeypatch):
    """With reading on, files match on their pages too; they take no place."""
    monkeypatch.setattr(get_settings().reading, "enabled", True)
    me = caller_for(household["wouter"])
    for number in range(6):
        await save_as(
            me, "personal", f"route-{number}.md", f"Route {number}, some climbing."
        )
    await _climbing_files(me, 6)
    result = await find.brief(me, "", "climbing")
    assert len(result["engrams"]) == find.BRIEF_ENGRAMS == 5


async def test_brief_searches_as_before_with_the_switch_off(household, monkeypatch):
    """Off, brief asks the search exactly what it asked before files had text."""
    asked: list[dict] = []
    search = find.search_engrams

    async def spy(*args, **kwargs):
        """Note the keywords the search is asked with, and search.

        :returns: the search's hits
        """
        asked.append(kwargs)
        return await search(*args, **kwargs)

    monkeypatch.setattr(find, "search_engrams", spy)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "route.md", "Some climbing.")
    await find.brief(me, "", "climbing")
    assert asked == [{"log": False, "files": True, "earlier": []}]


async def test_brief_without_matches_is_empty(household):
    me = caller_for(household["wouter"])
    assert await find.brief(me, "", "zeppelin") == {"engrams": []}


async def test_brief_never_shows_what_the_caller_may_not_read(household):
    partner = caller_for(household["partner"])
    await save_as(partner, "personal", "secret.md", "The zeppelin plan.")
    me = caller_for(household["wouter"])
    assert await find.brief(me, "", "zeppelin") == {"engrams": []}


async def test_brief_clamps_entries(household):
    me = caller_for(household["wouter"])
    await save_as(
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
        await save_as(me, "personal", path, _entries(1, 1), tags=["log"])
    await save_as(me, "personal", "a-log-4.md", _entries(1, 1))
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
    await save_as(me, "personal", "note.md", "Not a log.")
    assert await logs.series(me, "personal", "note.md") == []
    assert await logs.series(me, "personal", "missing.md") == []


async def test_last_entries_use_level_two_without_level_three(household):
    me = caller_for(household["wouter"])
    body = "# Log\n\nIntro.\n\n## One\nx\n\n## Two\ny\n"
    await save_as(me, "personal", "l.md", body, tags=["log"])
    parts = await logs.series(me, "personal", "l.md")
    assert logs.last_entries(parts, 5) == ["## One\nx", "## Two\ny"]
    assert logs.last_entries(parts, 0) == []


def test_last_entries_skip_a_tilde_line_inside_a_backtick_fence():
    """A fence closes only on its own marker, as when sections are cut."""
    body = "### Day 1\n```\n~~~\n### not an entry\n```\n\n### Day 2\nx\n"
    entries = logs.last_entries([Engram(body=body, tags=["log"])], 5)
    assert [entry.splitlines()[0] for entry in entries] == ["### Day 1", "### Day 2"]
