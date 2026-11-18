"""The search log: off unless switched on, strictly per person, ninety days."""

import json
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import asyncpg
import pytest

from reef import searchlog, usage
from reef.access import Principal, arm
from reef.config import get_settings
from reef.db import transaction_scope
from reef.export import build_full_dump
from reef.models import SearchLog
from reef.pages import save_page
from reef.search import search_pages
from reef.server import tool_read_page, tool_search
from reef.worker import purge_search_log


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


@pytest.fixture
def logging_on(monkeypatch):
    """Switch search logging on, as the operator would."""
    monkeypatch.setattr(get_settings(), "search_log", True)


async def _rows(principal) -> list[dict]:
    """Return the search log as the principal sees it, oldest first."""
    await arm(principal)
    return await SearchLog.select().order_by(SearchLog.searched_at)


# --- what is recorded ----------------------------------------------------------


async def test_nothing_is_recorded_unless_switched_on(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "boiler.md", "A Vaillant boiler.", message="x")
    await tool_search(me, "boiler")
    assert await _rows(me) == []


async def test_a_search_records_the_query_and_the_pages_returned(
    tx, household, logging_on
):
    me = principal_for(household["wouter"])
    page = await save_page(
        me, "personal", "boiler.md", "A Vaillant boiler.", message="x"
    )
    await tool_search(me, "  boiler  ")
    [row] = await _rows(me)
    assert row["query"] == "boiler"
    assert row["page_ids"] == [page.id]
    assert row["person_id"] == me.person_id


async def test_a_search_that_finds_nothing_is_recorded_too(tx, household, logging_on):
    me = principal_for(household["wouter"])
    await tool_search(me, "zeppelin")
    [row] = await _rows(me)
    assert (row["query"], row["page_ids"]) == ("zeppelin", [])


async def test_a_script_that_searches_is_not_recorded(tx, household, logging_on):
    """The benchmark harness calls search too. It is nobody's question."""
    me = principal_for(household["wouter"])
    await search_pages(me, "boiler")
    assert await _rows(me) == []


async def test_an_empty_query_is_not_recorded(tx, household, logging_on):
    me = principal_for(household["wouter"])
    await tool_search(me, "   ")
    assert await _rows(me) == []


async def test_a_long_query_is_cut_and_the_search_still_answers(
    tx, household, logging_on
):
    me = principal_for(household["wouter"])
    await tool_search(me, "boiler " * 200)
    [row] = await _rows(me)
    assert len(row["query"]) == 500


# --- whose it is ---------------------------------------------------------------


async def test_a_person_sees_only_their_own_searches(household, logging_on):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_page(me, "household", "boiler.md", "A boiler.", message="x")
        await tool_search(me, "boiler")
    async with transaction_scope():
        assert await _rows(partner) == []
        assert await searchlog.misses(partner) == []
    async with transaction_scope():
        assert len(await _rows(me)) == 1


async def test_a_search_cannot_be_logged_in_somebody_elses_name(household):
    me = principal_for(household["wouter"])
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(me)
            await SearchLog.raw(
                "INSERT INTO search_log (id, person_id, query) VALUES ({}, {}, {})",
                uuid4(),
                household["partner"].id,
                "planted",
            )


async def test_a_logged_search_cannot_be_rewritten(household, logging_on):
    """No ``UPDATE`` policy exists, so an update reaches no row.

    In production the app role also lacks the ``UPDATE`` privilege, and the
    statement is refused outright. The test role owns its tables.
    """
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await tool_search(me, "boiler")
    async with transaction_scope():
        await arm(me)
        await SearchLog.raw("UPDATE search_log SET query = 'other'")
    async with transaction_scope():
        assert [row["query"] for row in await _rows(me)] == ["boiler"]


async def test_a_person_may_delete_their_own_searches(household, logging_on):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await tool_search(me, "boiler")
    async with transaction_scope():
        await tool_search(partner, "garden")
    async with transaction_scope():
        await arm(me)
        await SearchLog.raw("DELETE FROM search_log")
    async with transaction_scope():
        assert await _rows(me) == []
    async with transaction_scope():
        assert len(await _rows(partner)) == 1


async def test_deleting_a_person_takes_their_searches_along(
    household, graph, seed, logging_on
):
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await tool_search(partner, "garden")
    await seed.execute("DELETE FROM coves WHERE id = $1", household["p_personal"].id)
    await graph.erase_person(household["partner"])
    assert await seed.fetchval("SELECT count(*) FROM search_log") == 0


# --- how long ------------------------------------------------------------------


async def test_old_searches_are_deleted_for_everybody_and_young_ones_kept(
    household, seed, logging_on
):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    for person, query in ((me, "old"), (partner, "old"), (me, "young")):
        async with transaction_scope():
            await tool_search(person, query)
    await seed.execute(
        "UPDATE search_log SET searched_at = searched_at - interval '91 days' "
        "WHERE query = 'old'"
    )
    assert await purge_search_log() == 2
    left = await seed.fetch("SELECT query FROM search_log")
    assert [row["query"] for row in left] == ["young"]
    assert await purge_search_log() == 0


async def test_the_purge_cannot_be_asked_for_a_shorter_time(seed):
    """It takes no argument, so no caller can delete younger rows."""
    arguments = await seed.fetchval(
        "SELECT pronargs FROM pg_proc WHERE proname = 'reef_purge_search_log'"
    )
    assert arguments == 0


# --- what it is for ------------------------------------------------------------


async def test_a_read_of_a_page_the_search_did_not_return_is_a_miss(
    tx, household, logging_on, monkeypatch
):
    monkeypatch.setattr(usage, "current_session_id", lambda: "s1")
    monkeypatch.setattr(searchlog, "current_session_id", lambda: "s1")
    me = principal_for(household["wouter"])
    found = await save_page(me, "personal", "boiler.md", "A boiler.", message="x")
    missed = await save_page(me, "personal", "heating.md", "Vaillant.", message="x")
    await tool_search(me, "boiler")
    await tool_read_page(me, "personal", "boiler.md")
    await tool_read_page(me, "personal", "heating.md")
    misses = await searchlog.misses(me)
    assert [(m["query"], m["page_id"]) for m in misses] == [("boiler", missed.id)]
    assert found.id != missed.id


async def test_a_read_in_another_session_is_not_a_miss(
    tx, household, logging_on, monkeypatch
):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "heating.md", "Vaillant.", message="x")
    monkeypatch.setattr(searchlog, "current_session_id", lambda: "s1")
    await tool_search(me, "boiler")
    monkeypatch.setattr(usage, "current_session_id", lambda: "s2")
    await tool_read_page(me, "personal", "heating.md")
    assert await searchlog.misses(me) == []


async def test_the_full_export_carries_only_the_persons_own_searches(
    tx, household, logging_on
):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await save_page(me, "household", "boiler.md", "A boiler.", message="x")
    await tool_search(me, "boiler")
    await tool_search(partner, "garden")
    with ZipFile(BytesIO(await build_full_dump(me))) as archive:
        searches = json.loads(archive.read("data/searches.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert [(s["query"], s["returned"]) for s in searches] == [
        ("boiler", [{"cove": "household", "path": "boiler.md"}])
    ]
    assert manifest["contents"]["search_log"] == 1
