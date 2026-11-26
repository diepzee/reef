"""Typed-link rights are enforced for the application's database role."""

from uuid import uuid4

import asyncpg
import pytest
from conftest import caller_for, save_as


async def insert_link(
    probe, person, source, target, kind="supersedes", origin="author"
):
    """Insert a link as the supplied person.

    :param probe: the application's connection
    :param person: the acting person
    :param source: the source engram
    :param target: the target engram
    :param kind: the relation kind
    :param origin: how the link was made
    :returns: the link id
    """
    link_id = uuid4()
    async with probe.transaction():
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)", str(person.id)
        )
        await probe.execute(
            "INSERT INTO links (id, from_engram_id, to_engram_id, author_id, kind, source) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            link_id,
            source.id,
            target.id,
            person.id,
            kind,
            origin,
        )
    return link_id


async def retire(probe, person, source, kind="supersedes", keep=()):
    """Call retirement with the rights of one person.

    :param probe: the application's connection
    :param person: the acting person
    :param source: the source engram
    :param kind: the kind to retire
    :param keep: target ids to retain
    """
    async with probe.transaction():
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)", str(person.id)
        )
        await probe.execute(
            "SELECT reef_retire_typed_links($1, $2, $3::uuid[])",
            source.id,
            kind,
            list(keep),
        )


async def test_replaces_needs_target_write_access(household, graph, probe):
    """Viewing a target permits about, but does not permit replacing it."""
    person = household["wouter"]
    caller = caller_for(person)
    source = await save_as(caller, "personal", "new.md", "new")
    target = await save_as(caller, "household", "old.md", "old")
    await graph.set_role(person, household["shared"], "viewer")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await insert_link(probe, person, source, target)
    await insert_link(probe, person, source, target, "about")
    await graph.set_role(person, household["shared"], "member")
    await insert_link(probe, person, source, target)


async def test_retirement_changes_only_the_given_kind_and_keeps_targets(
    household, probe, seed
):
    """Retirement leaves other kinds, wiki links and retained rows alone."""
    person = household["wouter"]
    caller = caller_for(person)
    source = await save_as(caller, "household", "new.md", "new")
    old = await save_as(caller, "household", "old.md", "old")
    kept = await save_as(caller, "household", "kept.md", "kept")
    retired_id = await insert_link(probe, person, source, old)
    kept_id = await insert_link(probe, person, source, kept)
    about_id = await insert_link(probe, person, source, old, "about")
    wiki_id = await insert_link(probe, person, source, old, "references", "wikilink")
    await retire(probe, person, source, keep=[kept.id])
    rows = {row["id"]: row for row in await seed.fetch("SELECT * FROM links")}
    assert rows[retired_id]["status"] == "superseded"
    assert rows[retired_id]["superseded_at"] is not None
    for link_id in (kept_id, about_id, wiki_id):
        assert rows[link_id]["status"] == "active"


async def test_retirement_needs_source_write_access(household, graph, probe, seed):
    """A viewer or stranger cannot retire another person's typed links."""
    person = household["wouter"]
    caller = caller_for(person)
    source = await save_as(caller, "household", "new.md", "new")
    target = await save_as(caller, "household", "old.md", "old")
    await insert_link(probe, person, source, target)
    await graph.set_role(household["partner"], household["shared"], "viewer")
    await retire(probe, household["partner"], source)
    stranger = await graph.person("stranger@example.test", "Stranger")
    await retire(probe, stranger, source)
    assert await seed.fetchval("SELECT status FROM links") == "active"


async def test_retirement_leaves_unreadable_targets(household, graph, probe, seed):
    """A shared-space editor cannot remove a link they cannot see."""
    person = household["wouter"]
    await graph.shared_space("club", person)
    caller = caller_for(person)
    source = await save_as(caller, "household", "new.md", "new")
    target = await save_as(caller, "club", "old.md", "old")
    await insert_link(probe, person, source, target)
    await retire(probe, household["partner"], source)
    assert await seed.fetchval("SELECT status FROM links") == "active"
    await retire(probe, person, source)
    assert await seed.fetchval("SELECT status FROM links") == "superseded"
