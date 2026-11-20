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
from reef.engrams import save_engram
from reef.export import build_full_dump
from reef.methods import Caller, connector, find
from reef.models import SearchLog
from reef.search import search_engrams
from reef.worker import purge_search_log


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


def _caller(principal: Principal) -> Caller:
    """Build the caller the MCP transport would build.

    :param principal: the person
    :returns: the caller
    """
    return Caller(principal=principal, writer="assistant", session_id=None)


async def _search(principal: Principal, query: str) -> dict:
    """Search as an assistant does, through the connector's ``search``.

    :param principal: who searches
    :param query: the words searched for
    :returns: the connector payload
    """
    return await connector.search(_caller(principal), "", query)


async def _open(principal: Principal, space: str, path: str) -> None:
    """Open one engram as an assistant does.

    :param principal: who opens it
    :param space: the person's name for the space
    :param path: the engram's path
    """
    await find.open(_caller(principal), "", [f"{space}/{path}"])


@pytest.fixture
def logging_on(monkeypatch):
    """Switch search logging on, as the operator would."""
    monkeypatch.setattr(get_settings().search, "log", True)


async def _rows(principal) -> list[dict]:
    """Return the search log as the principal sees it, oldest first."""
    await arm(principal)
    return await SearchLog.select().order_by(SearchLog.searched_at)


# --- what is recorded ----------------------------------------------------------


async def test_nothing_is_recorded_unless_switched_on(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "boiler.md", "A Vaillant boiler.", message="x")
    await _search(me, "boiler")
    assert await _rows(me) == []


async def test_a_search_records_the_query_and_the_engrams_returned(
    tx, household, logging_on
):
    me = principal_for(household["wouter"])
    engram = await save_engram(
        me, "personal", "boiler.md", "A Vaillant boiler.", message="x"
    )
    await _search(me, "  boiler  ")
    [row] = await _rows(me)
    assert row["query"] == "boiler"
    assert row["engram_ids"] == [engram.id]
    assert row["person_id"] == me.person_id


async def test_a_search_that_finds_nothing_is_recorded_too(tx, household, logging_on):
    me = principal_for(household["wouter"])
    await _search(me, "zeppelin")
    [row] = await _rows(me)
    assert (row["query"], row["engram_ids"]) == ("zeppelin", [])


async def test_a_script_that_searches_is_not_recorded(tx, household, logging_on):
    """The benchmark harness calls search too. It is nobody's question."""
    me = principal_for(household["wouter"])
    await search_engrams(me, "boiler")
    assert await _rows(me) == []


async def test_an_empty_query_is_not_recorded(tx, household, logging_on):
    me = principal_for(household["wouter"])
    await _search(me, "   ")
    assert await _rows(me) == []


async def test_a_long_query_is_cut_and_the_search_still_answers(
    tx, household, logging_on
):
    me = principal_for(household["wouter"])
    await _search(me, "boiler " * 200)
    [row] = await _rows(me)
    assert len(row["query"]) == 500


async def test_a_search_still_answers_when_recording_fails(
    household, logging_on, monkeypatch, caplog
):
    """A refused row must not cost the person their results."""
    monkeypatch.setattr(searchlog, "SEARCH_LOG_MAX_QUERY", 10_000)
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "boiler.md", "A boiler.", message="x")
        results = await _search(me, "boiler " * 200)
        # The transaction is still usable after the refusal.
        assert await _rows(me) == []
    assert len(results["results"]) == 2
    assert "CheckViolationError" in caplog.text
    assert "boiler" not in caplog.text


# --- whose it is ---------------------------------------------------------------


async def test_a_person_sees_only_their_own_searches(household, logging_on):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_engram(me, "household", "boiler.md", "A boiler.", message="x")
        await _search(me, "boiler")
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
        await _search(me, "boiler")
    async with transaction_scope():
        await arm(me)
        await SearchLog.raw("UPDATE search_log SET query = 'other'")
    async with transaction_scope():
        assert [row["query"] for row in await _rows(me)] == ["boiler"]


async def test_a_person_may_delete_their_own_searches(household, logging_on):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await _search(me, "boiler")
    async with transaction_scope():
        await _search(partner, "garden")
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
        await _search(partner, "garden")
    await seed.execute("DELETE FROM spaces WHERE id = $1", household["p_personal"].id)
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
            await _search(person, query)
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


async def test_a_read_of_an_engram_the_search_did_not_return_is_a_miss(
    tx, household, logging_on, monkeypatch
):
    monkeypatch.setattr(usage, "current_session_id", lambda: "s1")
    monkeypatch.setattr(searchlog, "current_session_id", lambda: "s1")
    me = principal_for(household["wouter"])
    found = await save_engram(me, "personal", "boiler.md", "A boiler.", message="x")
    missed = await save_engram(me, "personal", "heating.md", "Vaillant.", message="x")
    await _search(me, "boiler")
    await _open(me, "personal", "boiler.md")
    await _open(me, "personal", "heating.md")
    misses = await searchlog.misses(me)
    assert [(m["query"], m["engram_id"]) for m in misses] == [("boiler", missed.id)]
    assert found.id != missed.id


async def test_a_read_in_another_session_is_not_a_miss(
    tx, household, logging_on, monkeypatch
):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "heating.md", "Vaillant.", message="x")
    monkeypatch.setattr(searchlog, "current_session_id", lambda: "s1")
    await _search(me, "boiler")
    monkeypatch.setattr(usage, "current_session_id", lambda: "s2")
    await _open(me, "personal", "heating.md")
    assert await searchlog.misses(me) == []


async def test_the_full_export_carries_only_the_persons_own_searches(
    tx, household, logging_on
):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await save_engram(me, "household", "boiler.md", "A boiler.", message="x")
    await _search(me, "boiler")
    await _search(partner, "garden")
    with ZipFile(BytesIO(await build_full_dump(me))) as archive:
        searches = json.loads(archive.read("data/searches.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert [(s["query"], s["returned"]) for s in searches] == [
        ("boiler", [{"space": "household", "path": "boiler.md"}])
    ]
    assert manifest["contents"]["search_log"] == 1
