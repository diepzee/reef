"""The usage log and activation: real use only, and strictly per person."""

import math
from io import BytesIO
from zipfile import ZipFile

import asyncpg
import pytest
from conftest import _login

from reef import usage
from reef.access import Principal, arm
from reef.config import get_settings
from reef.context import build_index, load_context
from reef.db import transaction_scope
from reef.export import build_full_dump
from reef.jobs import REFRESH_ACTIVATION
from reef.models import AccessKind, Job, PageAccess, PageHeat
from reef.pages import save_page
from reef.search import search_pages
from reef.server import tool_read_page, tool_remember
from reef.usage import activations, record, refresh_activation

DECAY = get_settings().usage.decay
WINDOW = get_settings().usage.window
from reef.worker import drain


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def _log(principal) -> list[tuple[str, float]]:
    """Return the principal's usage log as ``(via, weight)``, oldest first."""
    await arm(principal)
    rows = await PageAccess.select().order_by(PageAccess.accessed_at)
    return [(row["via"], row["weight"]) for row in rows]


async def _age(seed, hours: float, via: str | None = None) -> None:
    """Move usage rows into the past, all of them or one kind."""
    await seed.execute(
        "UPDATE page_access SET accessed_at = accessed_at - make_interval(secs => $1) "
        "WHERE $2::text IS NULL OR via = $2",
        hours * 3600,
        via,
    )


# --- what counts as use --------------------------------------------------------


async def test_writing_and_reading_a_page_are_recorded(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "house.md", "x", message="x")
    await tool_read_page(me, "personal", "house.md")
    assert await _log(me) == [("write", 1.0), ("read", 1.0)]


async def test_something_the_person_asked_to_remember_counts_three_times(tx, household):
    me = principal_for(household["wouter"])
    await tool_remember(me, "The boiler is a Vaillant.", "personal")
    assert await _log(me) == [("remember", 3.0)]


async def test_being_shown_is_not_being_used(tx, household):
    """Search results, the index and a bulk load record nothing."""
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "house.md", "The boiler.", message="x", via=None)
    assert await search_pages(me, "boiler") != []
    await build_index(me)
    await load_context(me, char_budget=10_000)
    assert await _log(me) == []


async def test_a_miss_and_a_read_of_the_past_record_nothing(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "house.md", "x", message="x", via=None)
    assert (await tool_read_page(me, "personal", "nope.md"))["error"] == "not_found"
    await tool_read_page(me, "personal", "house.md", as_of="2999-01-01T00:00:00")
    assert await _log(me) == []


async def test_opening_a_page_in_the_web_interface_is_recorded(api, world, seed):
    alice, _bob, _team = world
    async with transaction_scope():
        await save_page(
            principal_for(alice), "team", "plan.md", "x", message="x", via=None
        )
    _login(api, alice)
    assert (await api.get("/api/pages/team/plan.md")).status_code == 200
    rows = await seed.fetch("SELECT via, person_id, session_id FROM page_access")
    assert [tuple(row) for row in rows] == [("web", alice.id, None)]


# --- whose it is ---------------------------------------------------------------


async def test_one_members_use_of_a_shared_page_is_invisible_to_another(
    household, seed
):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_page(me, "household", "house.md", "x", message="x")
        await tool_read_page(me, "household", "house.md")
        await refresh_activation(me)
    async with transaction_scope():
        assert await _log(partner) == []
        assert await activations(partner) == {}
        assert await PageHeat.count() == 0
    # Unarmed sees nothing either.
    assert await PageAccess.count() == 0
    assert await PageHeat.count() == 0
    assert await seed.fetchval("SELECT count(*) FROM page_access") == 2


async def test_use_cannot_be_recorded_in_somebody_elses_name(household):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        page = await save_page(me, "household", "house.md", "x", message="x")
    for table, extra in (
        ("page_access", ""),
        ("page_heat", ", activation, accesses, last_access"),
    ):
        values = ", 1, 1, LOCALTIMESTAMP" if extra else ""
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with transaction_scope():
                await arm(me)
                await PageAccess.raw(
                    f"INSERT INTO {table} (id, person_id, page_id{extra}) "
                    f"VALUES (gen_random_uuid(), {{}}, {{}}{values})",
                    household["partner"].id,
                    page.id,
                )


async def test_use_of_a_page_the_person_cannot_read_records_nothing(household, seed):
    """Silently, and the same as for a page that does not exist.

    Silently because a refusal would abort the caller's transaction, and
    the caller can be someone whose access ended a moment ago. The same
    either way because a difference would say whether the page exists.
    """
    from uuid import uuid4

    partner = principal_for(household["partner"])
    async with transaction_scope():
        page = await save_page(
            partner, "personal", "diary.md", "x", message="x", via=None
        )
    async with transaction_scope():
        me = principal_for(household["wouter"])
        await record(me, page.id, AccessKind.READ)
        await record(me, uuid4(), AccessKind.READ)
    assert await seed.fetchval("SELECT count(*) FROM page_access") == 0
    assert await seed.fetchval("SELECT count(*) FROM jobs") == 0


async def test_the_database_itself_refuses_use_of_an_unreadable_page(household):
    """The quiet path above is a courtesy. This is the rule behind it."""
    partner = principal_for(household["partner"])
    async with transaction_scope():
        page = await save_page(
            partner, "personal", "diary.md", "x", message="x", via=None
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(principal_for(household["wouter"]))
            await PageAccess(person_id=household["wouter"].id, page_id=page.id).save()


async def test_a_read_survives_losing_access_before_it_is_recorded(household, graph):
    """Membership removed between the read and the record: no error."""
    partner = principal_for(household["partner"])
    async with transaction_scope():
        page = await save_page(
            partner, "household", "house.md", "x", message="x", via=None
        )
    await graph.drop_membership(household["partner"], household["shared"])
    async with transaction_scope():
        await record(partner, page.id, AccessKind.READ)
        assert await _log(partner) == []


async def test_the_log_cannot_be_rewritten_but_a_person_may_delete_their_own(
    household, seed
):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_page(me, "household", "house.md", "x", message="x")
        await save_page(partner, "household", "garden.md", "x", message="x")
    async with transaction_scope():
        await arm(me)
        changed = await PageAccess.raw("UPDATE page_access SET weight = 9 RETURNING id")
        deleted = await PageAccess.raw("DELETE FROM page_access RETURNING id")
    assert changed == []
    assert len(deleted) == 1
    assert await seed.fetchval("SELECT count(*) FROM page_access") == 1
    assert await seed.fetchval("SELECT max(weight) FROM page_access") == 1.0


async def test_leaving_a_cove_hides_the_record_of_using_its_pages(household, graph):
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_page(partner, "household", "house.md", "x", message="x")
        await refresh_activation(partner)
    await graph.drop_membership(household["partner"], household["shared"])
    async with transaction_scope():
        assert await _log(partner) == []
        assert await activations(partner) == {}
    async with transaction_scope():
        await save_page(me, "household", "other.md", "x", message="x")


async def test_deleting_a_person_takes_their_usage_along(household, graph, seed):
    partner = principal_for(household["partner"])
    async with transaction_scope():
        await save_page(partner, "household", "house.md", "x", message="x")
        await refresh_activation(partner)
    await seed.execute(
        "DELETE FROM pages WHERE cove_id = $1", household["p_personal"].id
    )
    await seed.execute("DELETE FROM coves WHERE id = $1", household["p_personal"].id)
    await graph.erase_person(household["partner"])
    assert await seed.fetchval("SELECT count(*) FROM page_access") == 0
    assert await seed.fetchval("SELECT count(*) FROM page_heat") == 0
    assert await seed.fetchval("SELECT count(*) FROM pages") == 1


# --- activation ----------------------------------------------------------------


async def test_activation_follows_the_formula(household, seed):
    """Three uses, 1, 4 and 100 hours old, the oldest one weighted three."""
    me = principal_for(household["wouter"])
    async with transaction_scope():
        page = await save_page(me, "personal", "house.md", "x", message="x")
        await tool_read_page(me, "personal", "house.md")
        await tool_remember(me, "x", "personal")
    await seed.execute("DELETE FROM page_access WHERE page_id <> $1", page.id)
    await seed.execute(
        "INSERT INTO page_access (id, person_id, page_id, via, weight) "
        "VALUES (gen_random_uuid(), $1, $2, 'remember', 3)",
        me.person_id,
        page.id,
    )
    await _age(seed, 1, "write")
    await _age(seed, 4, "read")
    await _age(seed, 100, "remember")
    async with transaction_scope():
        assert await refresh_activation(me) == 1
        stored = (await activations(me))[page.id]
    expected = math.log(1**-DECAY + 4**-DECAY + 3 * 100**-DECAY)
    assert stored == pytest.approx(expected, abs=1e-3)


async def test_recent_and_frequent_use_both_raise_activation(household, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        old = await save_page(me, "personal", "old.md", "x", message="x")
    await _age(seed, 24 * 30)
    async with transaction_scope():
        once = await save_page(me, "personal", "once.md", "x", message="x")
        often = await save_page(me, "personal", "often.md", "x", message="x")
        for _ in range(4):
            await tool_read_page(me, "personal", "often.md")
    await _age(seed, 2)
    async with transaction_scope():
        await refresh_activation(me)
        scores = await activations(me)
    assert scores[often.id] > scores[once.id] > scores[old.id]


async def test_only_the_most_recent_uses_count(household, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        page = await save_page(me, "personal", "house.md", "x", message="x", via=None)
    await seed.execute(
        "INSERT INTO page_access (id, person_id, page_id, accessed_at) "
        "SELECT gen_random_uuid(), $1, $2, LOCALTIMESTAMP - make_interval(hours => n) "
        "FROM generate_series(1, $3) AS n",
        me.person_id,
        page.id,
        WINDOW + 25,
    )
    async with transaction_scope():
        await refresh_activation(me)
        stored = (await activations(me))[page.id]
        [row] = await PageHeat.select()
    expected = math.log(sum(hours**-DECAY for hours in range(1, WINDOW + 1)))
    assert stored == pytest.approx(expected, abs=1e-3)
    # The count is of every use, not only the ones that entered the sum.
    assert row["accesses"] == WINDOW + 25


async def test_a_use_from_this_second_does_not_swamp_everything(tx, household):
    me = principal_for(household["wouter"])
    page = await save_page(me, "personal", "house.md", "x", message="x")
    await refresh_activation(me)
    assert (await activations(me))[page.id] == pytest.approx(
        math.log(get_settings().usage.min_age_hours ** -DECAY), abs=1e-3
    )


async def test_refreshing_replaces_what_was_stored(tx, household, seed):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "x", message="x")
    assert await refresh_activation(me) == 1
    await save_page(me, "personal", "b.md", "x", message="x")
    assert await refresh_activation(me) == 2
    assert await PageHeat.count() == 2


# --- the refresh is queued by the person's own use ------------------------------


async def test_use_queues_one_refresh_which_the_worker_runs_later(household, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(me, "personal", "a.md", "x", message="x")
        await save_page(me, "personal", "b.md", "x", message="x")
        await tool_read_page(me, "personal", "a.md")
    async with transaction_scope():
        await arm(me)
        jobs = await Job.select().where(Job.kind == REFRESH_ACTIVATION)
    assert [job["status"] for job in jobs] == ["queued"]
    assert jobs[0]["run_after"] > jobs[0]["created_at"]

    # Not due yet: a busy session is refreshed once, afterwards.
    assert await drain("test-worker") == 0
    await seed.execute(
        "UPDATE jobs SET run_after = LOCALTIMESTAMP - interval '1 second'"
    )
    assert await drain("test-worker") == 1
    async with transaction_scope():
        assert len(await activations(me)) == 2


# --- sessions ------------------------------------------------------------------


async def test_there_is_no_session_outside_an_assistant_session():
    assert usage.current_session_id() is None


async def test_a_session_is_stored_as_a_digest_never_as_the_id(
    tx, household, monkeypatch, seed
):
    from fastmcp.server import dependencies

    class _Context:
        session_id = "the-real-transport-session-id"

    monkeypatch.setattr(dependencies, "get_context", lambda: _Context())
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "x", message="x")
    await tool_read_page(me, "personal", "a.md")
    await arm(me)
    sessions = {row["session_id"] for row in await PageAccess.select()}
    assert len(sessions) == 1
    [session] = sessions
    assert len(session) == 32
    assert "transport" not in session


# --- export --------------------------------------------------------------------


async def test_the_full_export_carries_links_and_only_the_persons_own_usage(
    tx, household
):
    import json

    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await save_page(me, "household", "a.md", "x", message="x")
    await save_page(partner, "household", "b.md", "[[a.md]]", message="x")
    await tool_read_page(partner, "household", "a.md")
    await refresh_activation(me)
    await refresh_activation(partner)

    with ZipFile(BytesIO(await build_full_dump(me))) as archive:
        links = json.loads(archive.read("data/links.json"))
        log = json.loads(archive.read("data/usage.json"))
        activation = json.loads(archive.read("data/activation.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert [(link["from"], link["to"], link["status"]) for link in links] == [
        (
            {"cove": "household", "path": "b.md"},
            {"cove": "household", "path": "a.md"},
            "active",
        )
    ]
    assert [(row["page"]["path"], row["via"]) for row in log] == [("a.md", "write")]
    assert [row["page"]["path"] for row in activation] == ["a.md"]
    assert manifest["contents"]["usage_log"] == 1
