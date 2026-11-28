"""Links between engrams: stored on save, visible only with both ends.

The behaviour is small. What needs proving is the boundary: a link, a
backlink or a count must never tell a reader that an engram they cannot read
exists, and a person must not be able to plant or remove links by hand.
"""

import asyncpg
import pytest

from reef import links
from reef.access import Principal, arm
from reef.context import build_index
from reef.db import DB, transaction_scope
from reef.engrams import delete_engram, get_engram, save_engram
from reef.jobs import BACKFILL_LINKS
from reef.links import backfill_links, links_of, parse_target
from reef.methods import Caller, find
from reef.models import Engram, Job, Link
from reef.worker import drain


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def _links(principal, alias, path) -> dict:
    """Return the links of one engram as ``principal`` sees them."""
    engram = await get_engram(principal, alias, path)
    return await links_of(principal, engram.id)


def _targets(entries: list[dict]) -> list[tuple[str, str]]:
    return [(entry["space"], entry["path"]) for entry in entries]


async def _inbound(principal, alias, path) -> int:
    index = await build_index(principal)
    space = next(space for space in index.spaces if space.alias == alias)
    return next(engram for engram in space.engrams if engram["path"] == path)["inbound"]


# --- storing ------------------------------------------------------------------


async def test_a_wiki_link_is_stored_and_read_from_both_ends(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me, "personal", "boiler.md", "Vaillant.", message="x", title="Boiler"
    )
    await save_engram(me, "personal", "house.md", "See [[boiler.md]].", message="x")

    house = await _links(me, "personal", "house.md")
    assert house["links"] == [
        {
            "space": "personal",
            "path": "boiler.md",
            "title": "Boiler",
            "kind": "references",
        }
    ]
    assert house["linked_from"] == []
    boiler = await _links(me, "personal", "boiler.md")
    assert _targets(boiler["linked_from"]) == [("personal", "house.md")]
    assert boiler["links"] == []


async def test_a_link_to_a_section_points_at_the_engram(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "boiler.md", "x", message="x")
    await save_engram(
        me, "personal", "house.md", "[[boiler.md#service]] [[boiler.md]]", message="x"
    )
    assert _targets((await _links(me, "personal", "house.md"))["links"]) == [
        ("personal", "boiler.md")
    ]


async def test_an_engram_linking_to_itself_and_links_in_code_store_nothing(
    tx, household, seed
):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "boiler.md", "x", message="x")
    await save_engram(
        me, "personal", "house.md", "[[house.md]] and `[[boiler.md]]`", message="x"
    )
    assert (await _links(me, "personal", "house.md"))["links"] == []


async def test_a_link_that_leaves_the_body_is_retired_and_kept(household, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "boiler.md", "x", message="x")
        await save_engram(me, "personal", "house.md", "See [[boiler.md]].", message="x")
        await save_engram(me, "personal", "house.md", "Nothing to see.", message="x")
        assert (await _links(me, "personal", "house.md"))["links"] == []
        assert (await _links(me, "personal", "boiler.md"))["linked_from"] == []
    rows = await seed.fetch("SELECT status, superseded_at FROM links")
    assert [row["status"] for row in rows] == ["superseded"]
    assert rows[0]["superseded_at"] is not None


async def test_a_link_written_again_is_a_new_row_beside_the_old(household, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "boiler.md", "x", message="x")
        for body in (
            "[[boiler.md]]",
            "gone",
            "[[boiler.md]] back",
            "[[boiler.md]] again",
        ):
            await save_engram(me, "personal", "house.md", body, message="x")
    statuses = await seed.fetch("SELECT status FROM links ORDER BY recorded_at")
    assert sorted(row["status"] for row in statuses) == ["active", "superseded"]


async def test_a_link_written_before_its_target_connects_when_the_target_appears(
    tx, household
):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "house.md", "See [[ boiler.md#x ]].", message="x")
    assert (await _links(me, "personal", "house.md"))["links"] == []
    await save_engram(me, "personal", "boiler.md", "x", message="x")
    assert _targets((await _links(me, "personal", "house.md"))["links"]) == [
        ("personal", "boiler.md")
    ]


async def test_a_waiting_cross_space_link_connects_from_the_creators_personal_space(
    tx, household
):
    """Only the creator writes there, so the space name in it is theirs."""
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "diary.md", "[[household:house.md]]", message="x")
    await save_engram(me, "household", "house.md", "x", message="x")
    assert _targets((await _links(me, "household", "house.md"))["linked_from"]) == [
        ("personal", "diary.md")
    ]


async def test_a_waiting_link_never_connects_a_shared_engram_to_a_new_personal_one(
    household, seed
):
    """The same rule as on save, reached from the other direction."""
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(
            me, "household", "house.md", "[[personal:diary.md]]", message="x"
        )
        await save_engram(me, "personal", "diary.md", "x", message="x")
    assert await seed.fetchval("SELECT count(*) FROM links") == 0


async def test_deleting_an_engram_takes_its_links_in_both_directions(household, seed):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "a.md", "[[b.md]]", message="x")
        await save_engram(me, "personal", "b.md", "[[a.md]] [[c.md]]", message="x")
        await save_engram(me, "personal", "c.md", "x", message="x")
        await save_engram(me, "personal", "a.md", "[[b.md]] again", message="x")
    assert await seed.fetchval("SELECT count(*) FROM links") == 3
    async with transaction_scope():
        await delete_engram(me, "personal", "b.md")
    assert await seed.fetchval("SELECT count(*) FROM links") == 0


# --- who can see a link --------------------------------------------------------


async def test_a_backlink_from_an_engram_the_reader_cannot_read_does_not_exist(
    tx, household
):
    """Not as a backlink and not in a count: no way to learn the engram is there."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await save_engram(partner, "household", "house.md", "The house.", message="x")
    await save_engram(
        me, "personal", "diary.md", "About [[household:house.md]].", message="x"
    )

    mine = await _links(me, "household", "house.md")
    assert _targets(mine["linked_from"]) == [("personal", "diary.md")]
    assert await _inbound(me, "household", "house.md") == 1

    theirs = await _links(partner, "household", "house.md")
    assert theirs == {"links": [], "linked_from": []}
    assert await _inbound(partner, "household", "house.md") == 0
    await arm(partner)
    assert await Link.count() == 0


async def test_an_unarmed_connection_sees_no_links(household):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "a.md", "x", message="x")
        await save_engram(me, "personal", "b.md", "[[a.md]]", message="x")
    assert await Link.count() == 0


async def test_a_link_from_a_shared_space_into_a_personal_one_is_never_stored(
    household, seed
):
    """Whoever saves, and whatever "personal" means to them."""
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "diary.md", "x", message="x")
        await save_engram(
            me, "household", "house.md", "[[personal:diary.md]]", message="x"
        )
    assert await seed.fetchval("SELECT count(*) FROM links") == 0


async def test_the_database_refuses_that_link_even_when_asked_directly(household):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        diary = await save_engram(me, "personal", "diary.md", "x", message="x")
        house = await save_engram(me, "household", "house.md", "x", message="x")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(me)
            await Link(
                from_engram_id=house.id, to_engram_id=diary.id, author_id=me.person_id
            ).save()


async def test_links_read_with_the_readers_own_names_for_spaces(tx, household, seed):
    """Two people may call one space different things."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await seed.execute(
        "UPDATE memberships SET alias = 'home' WHERE person_id = $1 AND alias = 'household'",
        household["partner"].id,
    )
    await save_engram(me, "household", "a.md", "x", message="x")
    await save_engram(me, "household", "b.md", "[[a.md]]", message="x")
    assert _targets((await _links(me, "household", "b.md"))["links"]) == [
        ("household", "a.md")
    ]
    assert _targets((await _links(partner, "home", "b.md"))["links"]) == [
        ("home", "a.md")
    ]


# --- what a person may do to the table by hand ---------------------------------


@pytest.fixture
async def two_engrams(household):
    """A live link from ``b.md`` to ``a.md`` in the shared space, committed."""
    me = principal_for(household["wouter"])
    async with transaction_scope():
        a = await save_engram(me, "household", "a.md", "x", message="x")
        b = await save_engram(me, "household", "b.md", "[[a.md]]", message="x")
    return a, b


async def test_a_viewer_cannot_add_a_link(household, graph, two_engrams):
    a, b = two_engrams
    person = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_space(person, slug="viewer")
    await graph.add_membership(person, household["shared"], "viewer", alias="household")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(principal_for(person))
            await Link(
                from_engram_id=a.id, to_engram_id=b.id, author_id=person.id
            ).save()


async def test_a_link_cannot_be_added_in_somebody_elses_name(household, two_engrams):
    a, b = two_engrams
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with transaction_scope():
            await arm(principal_for(household["wouter"]))
            await Link(
                from_engram_id=a.id,
                to_engram_id=b.id,
                author_id=household["partner"].id,
            ).save()


async def test_a_link_cannot_be_changed_or_deleted_by_hand(
    household, two_engrams, seed
):
    """There is no policy for either, so both touch nothing."""
    async with transaction_scope():
        await arm(principal_for(household["wouter"]))
        assert await Link.count() == 1
        changed = await Link.raw("UPDATE links SET status = 'rejected' RETURNING id")
        deleted = await Link.raw("DELETE FROM links RETURNING id")
    assert changed == [] and deleted == []
    assert await seed.fetchval("SELECT status FROM links") == "active"


async def test_an_unknown_kind_is_refused(household, two_engrams):
    a, b = two_engrams
    with pytest.raises(asyncpg.CheckViolationError):
        async with transaction_scope():
            await arm(principal_for(household["wouter"]))
            await Link(
                from_engram_id=a.id,
                to_engram_id=b.id,
                kind="likes",
                author_id=household["wouter"].id,
            ).save()


# --- retiring: the one thing done past row security ----------------------------


async def test_the_retire_function_is_owned_closed_and_returns_nothing():
    [row] = await DB._run_in_new_connection(
        "SELECT pg_get_function_result(p.oid) AS result, prosecdef, proconfig, "
        "has_function_privilege('public', p.oid, 'EXECUTE') AS open "
        "FROM pg_proc p WHERE p.proname = 'reef_retire_links'"
    )
    assert row["result"] == "void"
    assert row["prosecdef"] is True
    assert "search_path=pg_catalog, public, pg_temp" in row["proconfig"]
    assert row["open"] is False


async def test_retiring_does_nothing_for_someone_who_may_not_change_the_engram(
    household, graph, two_engrams, seed
):
    """A stranger, a viewer, and an unarmed caller: all three change nothing."""
    _a, b = two_engrams
    await seed.execute(
        "UPDATE engrams SET reference_targets = '{}' WHERE id = $1", b.id
    )
    stranger = await graph.person("stranger@example.test", "Stranger")
    await graph.personal_space(stranger, slug="stranger")
    viewer = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_space(viewer, slug="viewer")
    await graph.add_membership(viewer, household["shared"], "viewer", alias="household")

    for person in (stranger, viewer):
        async with transaction_scope():
            await arm(principal_for(person))
            await Link.raw("SELECT reef_retire_links({})", b.id)
    await Link.raw("SELECT reef_retire_links({})", b.id)
    assert await seed.fetchval("SELECT status FROM links") == "active"

    async with transaction_scope():
        await arm(principal_for(household["partner"]))
        await Link.raw("SELECT reef_retire_links({})", b.id)
    assert await seed.fetchval("SELECT status FROM links") == "superseded"


async def test_an_editor_who_cannot_see_a_link_neither_breaks_nor_keeps_it(
    household, graph, seed
):
    """The link's other end is in a space the editor is not in.

    Editing around it must leave it alone. Removing its words must end it.
    Neither may depend on the editor being able to see the link.
    """
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await graph.shared_space("club", household["wouter"])
    async with transaction_scope():
        await save_engram(me, "club", "rules.md", "x", message="x")
        await save_engram(
            me, "household", "house.md", "See [[club:rules.md]].", message="x"
        )
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE status = 'active'") == 1
    )

    async with transaction_scope():
        assert (await _links(partner, "household", "house.md"))["links"] == []
        await save_engram(
            partner,
            "household",
            "house.md",
            "See [[club:rules.md]]. Tidied.",
            message="x",
        )
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE status = 'active'") == 1
    )

    async with transaction_scope():
        await save_engram(partner, "household", "house.md", "Tidied more.", message="x")
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE status = 'active'") == 0
    )


# --- surfaces ------------------------------------------------------------------


async def test_open_carries_links_and_backlinks(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "x", message="x")
    await save_engram(me, "personal", "b.md", "[[a.md]]", message="x")
    caller = Caller(principal=me, writer="assistant", session_id=None)
    [payload] = await find.open(caller, "", ["personal/a.md"])
    assert payload["links"] == []
    assert _targets(payload["linked_from"]) == [("personal", "b.md")]


async def test_the_index_counts_what_points_at_each_engram(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "x", message="x")
    await save_engram(me, "personal", "b.md", "[[a.md]]", message="x")
    await save_engram(me, "household", "c.md", "x", message="x")
    await save_engram(
        me, "personal", "d.md", "[[a.md]] [[household:c.md]]", message="x"
    )
    assert await _inbound(me, "personal", "a.md") == 2
    assert await _inbound(me, "personal", "b.md") == 0
    assert await _inbound(me, "household", "c.md") == 1


# --- engrams saved before links were stored ---------------------------------------


async def _forget_links(seed) -> None:
    """Make every engram look as it did before links were stored."""
    await seed.execute("DELETE FROM links")
    await seed.execute("UPDATE engrams SET linked_version = NULL")


async def test_backfill_stores_the_links_of_older_engrams_and_changes_nothing_else(
    household, seed
):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "a.md", "x", message="x")
        await save_engram(
            me, "personal", "b.md", "[[a.md]] [[household:c.md]]", message="x"
        )
        await save_engram(me, "household", "c.md", "[[personal:a.md]]", message="x")
        before = await build_index(me)
    await _forget_links(seed)
    await seed.execute(
        "UPDATE engrams SET indexed_version = NULL, reference_targets = NULL"
    )

    async with transaction_scope():
        assert await backfill_links(me) == 3
        assert await backfill_links(me) == 0
        assert await build_index(me) == before
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE status = 'active'") == 2
    )
    assert await seed.fetchval("SELECT count(*) FROM revisions") == 3
    assert await seed.fetchval("SELECT max(version) FROM engrams") == 1


async def test_loading_the_index_queues_the_link_backfill_and_the_worker_runs_it(
    household, seed
):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(me, "personal", "a.md", "x", message="x")
        await save_engram(me, "personal", "b.md", "[[a.md]]", message="x")
    await _forget_links(seed)
    async with transaction_scope():
        assert await _inbound(me, "personal", "a.md") == 0
        await arm(me)
        assert [
            job["kind"] for job in await Job.select().where(Job.kind == BACKFILL_LINKS)
        ] == [BACKFILL_LINKS]
    assert await drain("test-worker") == 1
    async with transaction_scope():
        assert await _inbound(me, "personal", "a.md") == 1
        await arm(me)
        assert [
            job["status"]
            for job in await Job.select().where(Job.kind == BACKFILL_LINKS)
        ] == ["done"]
        assert await Engram.count().where(Engram.linked_version.is_null()) == 0


async def test_a_viewer_backfills_nothing(household, graph, seed):
    owner = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(owner, "household", "a.md", "x", message="x")
        await save_engram(owner, "household", "b.md", "[[a.md]]", message="x")
    await _forget_links(seed)
    person = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_space(person, slug="viewer")
    await graph.add_membership(person, household["shared"], "viewer", alias="household")
    async with transaction_scope():
        assert await backfill_links(principal_for(person)) == 0
        await build_index(principal_for(person))
    assert await seed.fetchval("SELECT count(*) FROM links") == 0
    assert (
        await seed.fetchval("SELECT count(*) FROM jobs WHERE kind = $1", BACKFILL_LINKS)
        == 0
    )


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("plan.md", (None, "plan.md")),
        ("plan.md#q3", (None, "plan.md")),
        ("family:plan.md", ("family", "plan.md")),
        (" family : plan.md#q3 ", ("family", "plan.md")),
    ],
)
def test_a_written_target_splits_into_space_and_path(written, expected):
    assert parse_target(written) == expected


async def test_neighbourhood_reads_two_hops_both_ways_and_replaced_targets(
    tx, household
):
    """Two hops include neighbours' neighbours, but not a third link."""
    me = principal_for(household["wouter"])
    ids = []
    for name, body in (
        ("a", "[[b.md]]"),
        ("b", "[[c.md]]"),
        ("c", "[[d.md]]"),
        ("d", "end"),
    ):
        row = await save_engram(me, "personal", f"{name}.md", body, message="x")
        ids.append(row.id)
    a, b, c, d = ids
    await Link(
        from_engram_id=b,
        to_engram_id=c,
        kind="supersedes",
        source="author",
        author_id=me.person_id,
    ).save()
    neighbours, closed = await links.neighbourhood([a])
    assert neighbours == {a: [b], b: [a, c], c: [b]} or (
        {key: set(value) for key, value in neighbours.items()}
        == {a: {b}, b: {a, c}, c: {b}}
    )
    assert closed == {c}
    assert d not in neighbours


async def test_neighbourhood_ignores_retired_and_unreadable_links(tx, household):
    """Neither an old link nor another person's private endpoint exists."""
    me = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    a = await save_engram(me, "household", "a.md", "[[b.md]]", message="x")
    await save_engram(me, "household", "b.md", "end", message="x")
    await save_engram(me, "household", "a.md", "removed", message="x")
    private = await save_engram(
        me, "personal", "secret.md", "[[household:a.md]]", message="x"
    )
    await arm(me)
    neighbours, _ = await links.neighbourhood([a.id])
    assert neighbours == {a.id: [private.id], private.id: [a.id]}
    await arm(partner)
    assert await links.neighbourhood([a.id]) == ({}, set())
