"""Replacement ranking stays within the reader's view of the graph."""

from uuid import uuid4

import pytest
from conftest import caller_for, save_as, store_as
from test_search_files import reranker  # noqa: F401
from test_search_hybrid import service  # noqa: F401

from reef import embedding
from reef.config import get_settings
from reef.db import transaction_scope
from reef.links import replacements
from reef.models import LinkKind
from reef.relations import Relations
from reef.search import demote, search_engrams


def test_demote_keeps_groups_and_file_positions() -> None:
    """Only engram slots change when the replacements are already present."""
    old, new, other, older, file = [uuid4() for _ in range(5)]
    ranking = [
        ("page", old),
        ("file", file),
        ("page", other),
        ("page", older),
        ("page", new),
    ]
    assert demote(ranking, {old: [new], older: [new]}, {new}) == [
        ("page", other),
        ("file", file),
        ("page", new),
        ("page", old),
        ("page", older),
    ]
    assert demote(ranking, {}, set()) is ranking


def test_demote_inserts_only_a_missing_searchable_replacement() -> None:
    """An inserted replacement is adjacent and never duplicated."""
    old, new, other = [uuid4() for _ in range(3)]
    ranking = [("page", old), ("page", other)]
    assert demote(ranking, {old: [new]}, {new}) == [
        ("page", other),
        ("page", new),
        ("page", old),
    ]
    assert demote(ranking, {old: [new]}, set()) == [("page", other), ("page", old)]


def test_demote_inserts_first_searchable_replacement() -> None:
    """Skip replacements outside the searched spaces when inserting a hit."""
    old, newest, older = [uuid4() for _ in range(3)]
    ranking = [("page", old)]
    assert demote(ranking, {old: [newest, older]}, {older}) == [
        ("page", older),
        ("page", old),
    ]


def test_demote_after_cut_keeps_the_replacement() -> None:
    """A missing replacement can push its old hit out of a full result list."""
    old, new, *others = [uuid4() for _ in range(61)]
    candidates = [("page", row_id) for row_id in [old, *others]]
    hits = demote(candidates[:5], {old: [new]}, {new})[:5]
    assert hits == candidates[1:5] + [("page", new)]
    assert len(set(hits)) == len(hits)


def test_demote_keeps_both_when_present_or_when_room_remains() -> None:
    """An existing replacement stays before old; a missing one fills spare room."""
    old, new, *others = [uuid4() for _ in range(5)]
    ordinary = [("page", row_id) for row_id in others]
    expected = ordinary + [("page", new), ("page", old)]
    assert (
        demote([("page", old), *ordinary, ("page", new)], {old: [new]}, {new})
        == expected
    )
    assert demote([("page", old), *ordinary], {old: [new]}, {new})[:5] == expected


@pytest.mark.parametrize("use_reranker", [False, True])
async def test_candidate_cut_precedes_replacements(
    household, service, reranker, use_reranker, monkeypatch
) -> None:
    """Both vector and reranker paths replace an old hit within the asked five."""
    me = caller_for(household["wouter"])
    monkeypatch.setattr(
        service,
        "_vector",
        lambda text: [0.9, 0.1, 0.0, 0.0] if "note" in text else [1.0, 0.0, 0.0, 0.0],
    )
    for number in range(12):
        engram = await save_as(
            me,
            "personal",
            f"match-{number}.md",
            "car " * (12 - number),
            title="car" if number == 0 else f"Note {number}",
        )
        async with transaction_scope():
            assert await embedding.embed_engram(me.principal, engram.id, service) > 0
    await replace(me, "renewal.md", "match-0.md", "renewal")
    hits = await search_engrams(me.principal, "car", limit=5, rerank=use_reranker)
    paths = [hit["path"] for hit in hits]
    assert "renewal.md" in paths
    assert "match-0.md" not in paths
    assert len(paths) == len(set(paths)) == 5
    assert bool(reranker.documents) is use_reranker


async def replace(caller, path: str, target: str, body: str = "contract"):
    """Write a personal replacement with the caller's authority.

    :param caller: the writer
    :param path: the new engram
    :param target: the replaced engram
    :param body: the new text
    :returns: the saved engram
    """
    return await save_as(
        caller,
        "personal",
        path,
        body,
        channel="person",
        relations=Relations({LinkKind.SUPERSEDES: (target,)}),
    )


async def test_search_demotes_the_better_word_match(household) -> None:
    """The old text can match better and still comes after current text."""
    me = caller_for(household["wouter"])
    await save_as(
        me, "personal", "old.md", "contract contract contract", title="Contract"
    )
    await replace(me, "new.md", "old.md", "contract and many other words")
    hits = await search_engrams(me.principal, "contract", semantic=False, rerank=False)
    assert [hit["path"] for hit in hits] == ["new.md", "old.md"]
    assert hits[1]["replaced_by"] == [
        {"space": "personal", "path": "new.md", "title": "new"}
    ]
    assert "replaced_by" not in hits[0]


async def test_chain_ends_and_newest_replacement_first(household) -> None:
    """A chain names its end; a split lists newer replacement links first."""
    me = caller_for(household["wouter"])
    old = await save_as(me, "personal", "c.md", "contract")
    await replace(me, "b.md", "c.md")
    newest = await replace(me, "a.md", "b.md")
    split = await replace(me, "split.md", "c.md")
    async with transaction_scope():
        from reef.access import arm

        await arm(me.principal)
        assert await replacements([old.id]) == {old.id: [split.id, newest.id]}
    hits = await search_engrams(me.principal, "contract", semantic=False, rerank=False)
    hit = next(hit for hit in hits if hit["path"] == "c.md")
    assert [ref["path"] for ref in hit["replaced_by"]] == ["split.md", "a.md"]


async def test_missing_replacement_is_inserted_and_limit_holds(household) -> None:
    """A replacement with different words is still offered before old text."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "contract")
    await replace(me, "new.md", "old.md", "renewal")
    hits = await search_engrams(
        me.principal, "contract", limit=1, semantic=False, rerank=False
    )
    assert [hit["path"] for hit in hits] == ["new.md"]


async def test_space_filter_keeps_annotation_but_never_inserts_outside_it(
    household,
) -> None:
    """A personal replacement cannot become a hit in a shared-space search."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "old.md", "contract")
    await replace(me, "new.md", "household:old.md", "renewal")
    hits = await search_engrams(
        me.principal, "contract", space="household", semantic=False, rerank=False
    )
    assert [hit["path"] for hit in hits] == ["old.md"]
    assert hits[0]["replaced_by"][0]["space"] == "personal"


async def test_unreadable_replacement_changes_nothing(household) -> None:
    """Neither rank nor a note reveals a replacement the reader cannot see."""
    me = caller_for(household["wouter"])
    reader = caller_for(household["partner"])
    await save_as(me, "household", "old.md", "contract contract")
    await save_as(me, "household", "other.md", "contract")
    before = await search_engrams(
        reader.principal, "contract", semantic=False, rerank=False
    )
    await replace(me, "new.md", "household:old.md", "renewal")
    assert (
        await search_engrams(reader.principal, "contract", semantic=False, rerank=False)
        == before
    )


async def test_reading_files_still_leaves_room_for_engrams(
    household, monkeypatch
) -> None:
    """Replacement insertion still observes the five-result file limit."""
    monkeypatch.setattr(get_settings().reading, "enabled", True)
    monkeypatch.setattr(get_settings().search, "files_in_five", 3)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "contract")
    await replace(me, "new.md", "old.md", "renewal")
    for number in range(6):
        await store_as(
            me,
            "personal",
            f"contract-{number}.pdf",
            b"%PDF",
            "application/pdf",
            "contract contract contract",
        )
    hits = await search_engrams(
        me.principal, "contract", limit=5, semantic=False, rerank=False
    )
    assert sum(hit["kind"] == "file" for hit in hits) <= 3


@pytest.mark.parametrize("semantic", [False, True])
async def test_memory_without_links_has_a_fixed_order(
    household, service, monkeypatch, seed, semantic
) -> None:
    """Keep a fixed seeded order on both keyword and vector paths."""
    from datetime import datetime

    me = caller_for(household["wouter"])
    monkeypatch.setattr(
        service,
        "_vector",
        lambda text: (
            [0.8, 0.2, 0.0, 0.0]
            if "first" in text
            else [0.9, 0.1, 0.0, 0.0]
            if "second" in text
            else [1.0, 0.0, 0.0, 0.0]
        ),
    )
    for day, name in enumerate(("first", "second", "third"), 1):
        engram = await save_as(me, "personal", f"{name}.md", "car", title=name)
        await seed.execute(
            "UPDATE engrams SET updated_at = $1 WHERE id = $2",
            datetime(2026, 1, day),
            engram.id,
        )
        async with transaction_scope():
            assert await embedding.embed_engram(me.principal, engram.id, service) > 0
    hits = await search_engrams(me.principal, "car", semantic=semantic, rerank=False)
    assert [hit["path"] for hit in hits] == ["third.md", "second.md", "first.md"]
    assert all("replaced_by" not in hit for hit in hits)
