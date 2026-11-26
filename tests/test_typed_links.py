"""Relations are versioned with a save and obey the writer's rights."""

import pytest
from conftest import caller_for, save_as

from reef.db import transaction_scope
from reef.engrams import get_engram
from reef.links import links_of
from reef.models import Link, LinkKind
from reef.relations import RELATION_KEYS, Relations


async def test_common_save_reads_the_block(household) -> None:
    """A later whole-body caller gets the same parsing as the web route."""
    caller = caller_for(household["wouter"])
    await save_as(caller, "personal", "old.md", "old")
    new = await save_as(
        caller, "personal", "new.md", "---\nabout: [[old.md]]\n---\nnew"
    )
    assert new.body == "new"
    async with transaction_scope():
        assert (await links_of(caller.principal, new.id))["links"][0]["kind"] == "about"


@pytest.mark.parametrize("kind", RELATION_KEYS.values())
async def test_kind_is_stored_and_shown(household, kind) -> None:
    """Every written kind is an active author link and appears on open."""
    caller = caller_for(household["wouter"])
    await save_as(caller, "personal", "old.md", "old")
    new = await save_as(
        caller, "personal", "new.md", "new", relations=Relations({kind: ("old.md",)})
    )
    async with transaction_scope():
        shown = await links_of(caller.principal, new.id)
        assert shown["links"][0]["kind"] == kind
        [row] = await Link.select()
        assert row["source"] == "author"
        assert row["status"] == "active"


async def test_given_lists_replace_only_their_kind(household, seed) -> None:
    """Omission keeps rows; a new list retains matching rows; empty retires."""
    caller = caller_for(household["wouter"])
    for path in ("a.md", "b.md", "c.md"):
        await save_as(caller, "personal", path, path)
    await save_as(
        caller,
        "personal",
        "new.md",
        "[[c.md]]",
        relations=Relations(
            {LinkKind.ABOUT: ("a.md", "b.md"), LinkKind.CONTRADICTS: ("a.md",)}
        ),
    )
    before = await seed.fetch("SELECT * FROM links ORDER BY id")
    await save_as(caller, "personal", "new.md", "[[c.md]] again")
    assert await seed.fetch("SELECT * FROM links ORDER BY id") == before
    await save_as(
        caller,
        "personal",
        "new.md",
        "[[c.md]]",
        relations=Relations({LinkKind.ABOUT: ("b.md", "c.md")}),
    )
    rows = await seed.fetch("SELECT * FROM links WHERE status = 'active'")
    kept = next(
        row for row in before if row["kind"] == "about" and row["target_text"] == "b.md"
    )
    assert kept in rows
    assert len(rows) == 4
    await save_as(
        caller,
        "personal",
        "new.md",
        "no wiki",
        relations=Relations({LinkKind.ABOUT: ()}),
    )
    rows = await seed.fetch("SELECT kind FROM links WHERE status = 'active'")
    assert [row["kind"] for row in rows] == ["contradicts"]


@pytest.mark.parametrize(
    ("target", "source_space", "role", "code"),
    [
        ("missing.md", "personal", "member", "relation_target_missing"),
        ("hidden:old.md", "personal", "member", "relation_target_missing"),
        ("personal:old.md", "household", "member", "relation_into_personal"),
        ("new.md", "personal", "member", "relation_to_itself"),
        ("household:old.md", "personal", "viewer", "relation_needs_write"),
    ],
)
async def test_refusal_rolls_back_save(
    household, graph, target, source_space, role, code
) -> None:
    """An invalid relation leaves the previous body and version intact."""
    from reef.links import RelationRefused

    caller = caller_for(household["wouter"])
    for space in ("personal", "household"):
        await save_as(caller, space, "old.md", "old")
    original = await save_as(caller, source_space, "new.md", "before")
    await graph.set_role(household["wouter"], household["shared"], role)
    with pytest.raises(RelationRefused) as caught:
        await save_as(
            caller,
            source_space,
            "new.md",
            "after",
            relations=Relations({LinkKind.SUPERSEDES: (target,)}),
        )
    assert caught.value.code == code
    assert caught.value.target == target
    async with transaction_scope():
        current = await get_engram(caller.principal, source_space, "new.md")
        assert (current.body, current.version) == (original.body, original.version)


@pytest.mark.parametrize("length", [2, 3])
async def test_replacement_circles_are_refused(household, length) -> None:
    """A chain cannot lead back to its own source."""
    from reef.links import RelationRefused

    caller = caller_for(household["wouter"])
    for number in range(length):
        await save_as(caller, "personal", f"{number}.md", "body")
    for number in range(length - 1):
        await save_as(
            caller,
            "personal",
            f"{number}.md",
            "body",
            relations=Relations({LinkKind.SUPERSEDES: (f"{number + 1}.md",)}),
        )
    with pytest.raises(RelationRefused, match="relation_circle"):
        await save_as(
            caller,
            "personal",
            f"{length - 1}.md",
            "body",
            relations=Relations({LinkKind.SUPERSEDES: ("0.md",)}),
        )


async def test_two_replacements_and_later_wiki_link(household, seed) -> None:
    """Two sources may replace one target; a wiki link still has its own row."""
    caller = caller_for(household["wouter"])
    await save_as(caller, "personal", "old.md", "old")
    for path in ("a.md", "b.md"):
        await save_as(
            caller,
            "personal",
            path,
            "new",
            relations=Relations({LinkKind.SUPERSEDES: ("old.md",)}),
        )
    await save_as(caller, "personal", "a.md", "[[old.md]]")
    rows = await seed.fetch("SELECT kind FROM links WHERE status = 'active'")
    assert sorted(row["kind"] for row in rows) == [
        "references",
        "supersedes",
        "supersedes",
    ]


async def test_pending_replacements_uses_readers_names_and_existing_rows(
    household,
) -> None:
    """Only newly added pairs require confirmation."""
    from reef.links import Resolver, pending_replacements

    caller = caller_for(household["wouter"])
    await save_as(caller, "personal", "old.md", "old")
    relations = Relations({LinkKind.SUPERSEDES: ("old.md",)})
    async with transaction_scope():
        resolver = await Resolver.for_person(caller.principal)
        assert await pending_replacements(
            resolver, household["w_personal"].id, "new.md", relations
        ) == [("personal", "old.md")]
    await save_as(caller, "personal", "new.md", "new", relations=relations)
    async with transaction_scope():
        resolver = await Resolver.for_person(caller.principal)
        assert (
            await pending_replacements(
                resolver, household["w_personal"].id, "new.md", relations
            )
            == []
        )
