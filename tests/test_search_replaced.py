"""Replacement ranking stays within the reader's view of the graph."""

from uuid import uuid4

import pytest
from conftest import caller_for, save_as, store_as
from test_search_files import reranker as shared_reranker
from test_search_hybrid import service as shared_service

from reef import embedding
from reef.config import get_settings
from reef.db import transaction_scope
from reef.links import replacements
from reef.models import Link, LinkKind
from reef.relations import Relations
from reef.search import demote, search_engrams

reranker = shared_reranker
service = shared_service


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
    """An inserted replacement takes the old hit's original place."""
    old, new, other = [uuid4() for _ in range(3)]
    ranking = [("page", old), ("page", other)]
    assert demote(ranking, {old: [new]}, {new}) == [
        ("page", new),
        ("page", other),
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
    assert hits == [("page", new)] + candidates[1:5]
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
    assert demote([("page", old), *ordinary], {old: [new]}, {new})[:5] == [
        ("page", new),
        *ordinary,
        ("page", old),
    ]


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
    assert paths[0] == "renewal.md"
    assert hits[0]["replaces"] == [
        {"space": "personal", "path": "match-0.md", "title": "car"}
    ]
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


async def test_failed_replacement_query_keeps_search_working(
    household, monkeypatch, caplog
) -> None:
    """A failed graph query rolls back only its savepoint and logs no content."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "contract")
    await replace(me, "new.md", "old.md", "renewal")
    original = Link.raw

    def broken(statement, *params):
        """Make only the replacement lookup fail in PostgreSQL."""
        return (
            original("SELECT 1 / 0 /* private content */")
            if statement.startswith("WITH RECURSIVE")
            else original(statement, *params)
        )

    monkeypatch.setattr(Link, "raw", broken)
    hits = await search_engrams(me.principal, "contract", semantic=False, rerank=False)
    assert [hit["path"] for hit in hits] == ["old.md"]
    assert "replaced_by" not in hits[0]
    assert "DivisionByZeroError" in caplog.text
    assert "private content" not in caplog.text


async def test_seeded_circle_ends_and_is_not_replaced(household, seed) -> None:
    """A circle from imported data cannot loop or mark its members replaced."""
    me = caller_for(household["wouter"])
    a = await save_as(me, "personal", "a.md", "contract")
    b = await save_as(me, "personal", "b.md", "contract")
    for source, target in ((a, b), (b, a)):
        await seed.execute(
            "INSERT INTO links (id, from_engram_id, to_engram_id, author_id, kind, source) VALUES ($1, $2, $3, $4, 'supersedes', 'author')",
            uuid4(),
            source.id,
            target.id,
            me.principal.person_id,
        )
    hits = await search_engrams(me.principal, "contract", semantic=False, rerank=False)
    assert {hit["path"] for hit in hits} == {"a.md", "b.md"}
    assert all("replaced_by" not in hit for hit in hits)


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
    from datetime import UTC, datetime

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
            datetime(2026, 1, day, tzinfo=UTC).replace(tzinfo=None),
            engram.id,
        )
        async with transaction_scope():
            assert await embedding.embed_engram(me.principal, engram.id, service) > 0
    hits = await search_engrams(me.principal, "car", semantic=semantic, rerank=False)
    assert [hit["path"] for hit in hits] == ["third.md", "second.md", "first.md"]
    assert all("replaced_by" not in hit for hit in hits)


async def test_chain_does_not_cross_an_unreadable_middle(household, graph) -> None:
    """Readable ends cannot reveal a chain through an unreadable middle."""
    from reef.methods import find

    writer = caller_for(household["wouter"])
    reader = caller_for(household["partner"])
    await graph.shared_space("hidden", household["wouter"])
    await save_as(writer, "household", "c.md", "contract")
    await save_as(
        writer,
        "hidden",
        "b.md",
        "contract",
        channel="person",
        relations=Relations({LinkKind.SUPERSEDES: ("household:c.md",)}),
    )
    await save_as(
        writer,
        "household",
        "a.md",
        "renewal",
        channel="person",
        relations=Relations({LinkKind.SUPERSEDES: ("hidden:b.md",)}),
    )
    hits = await search_engrams(
        reader.principal, "contract", semantic=False, rerank=False
    )
    assert [hit["path"] for hit in hits] == ["c.md"]
    assert "replaced_by" not in hits[0]
    [opened] = await find.open(reader, "", ["household/c.md"])
    assert "replaced_by" not in opened


async def test_only_the_readable_of_two_replacements_is_named(household) -> None:
    """An unreadable split replacement affects neither search nor open."""
    from reef.methods import find

    writer = caller_for(household["wouter"])
    reader = caller_for(household["partner"])
    await save_as(writer, "household", "old.md", "contract")
    await save_as(
        writer,
        "household",
        "visible.md",
        "renewal",
        channel="person",
        relations=Relations({LinkKind.SUPERSEDES: ("old.md",)}),
    )
    await replace(writer, "hidden.md", "household:old.md", "private renewal")
    hits = await search_engrams(
        reader.principal, "contract", semantic=False, rerank=False
    )
    assert [hit["path"] for hit in hits] == ["visible.md", "old.md"]
    [opened] = await find.open(reader, "", ["household/old.md"])
    expected = [{"space": "household", "path": "visible.md", "title": "visible"}]
    assert hits[1]["replaced_by"] == opened["replaced_by"] == expected


@pytest.mark.parametrize("end", ["delete", "retire"])
async def test_ended_replacement_disappears_from_search_and_open(
    household, end
) -> None:
    """Deleting the new engram or retiring its link restores the old hit."""
    from reef.engrams import delete_engram
    from reef.methods import find

    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "contract")
    await replace(me, "new.md", "old.md", "renewal")
    if end == "delete":
        async with transaction_scope():
            await delete_engram(me.principal, "personal", "new.md")
    else:
        await save_as(
            me,
            "personal",
            "new.md",
            "renewal",
            relations=Relations({LinkKind.SUPERSEDES: ()}),
        )
    hits = await search_engrams(me.principal, "contract", semantic=False, rerank=False)
    assert [hit["path"] for hit in hits] == ["old.md"]
    [opened] = await find.open(me, "", ["personal/old.md"])
    assert "replaced_by" not in hits[0]
    assert "replaced_by" not in opened


async def test_shared_missing_replacement_is_inserted_once(household) -> None:
    """Two old hits sharing one missing replacement do not duplicate it."""
    me = caller_for(household["wouter"])
    for path in ("a.md", "b.md"):
        await save_as(me, "personal", path, "contract")
    await save_as(
        me,
        "personal",
        "new.md",
        "renewal",
        channel="person",
        relations=Relations({LinkKind.SUPERSEDES: ("a.md", "b.md")}),
    )
    hits = await search_engrams(me.principal, "contract", semantic=False, rerank=False)
    paths = [hit["path"] for hit in hits]
    assert paths[0] == "new.md"
    assert len(paths) == len(set(paths)) == 3
    assert {ref["path"] for ref in hits[0]["replaces"]} == {"a.md", "b.md"}


async def test_inserted_hit_explains_itself_in_search_recall_and_brief(
    household,
) -> None:
    """Search-derived payloads name the old hits the inserted note stands in for."""
    from reef.methods import find

    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "contract", title="Old contract")
    await replace(me, "new.md", "old.md", "renewal")
    hits = await search_engrams(
        me.principal, "contract", limit=1, semantic=False, rerank=False
    )
    recalled = await find.recall(me, "", "contract", limit=1)
    briefed = (await find.brief(me, "", "contract"))["engrams"]
    expected = [{"space": "personal", "path": "old.md", "title": "Old contract"}]
    for hit in (hits[0], recalled[0], briefed[0]):
        assert hit["replaces"] == expected
    await save_as(me, "personal", "new.md", "contract renewal")
    both = await search_engrams(me.principal, "contract", semantic=False, rerank=False)
    assert all("replaces" not in hit for hit in both)


async def test_inserted_replacement_keeps_room_after_reranking(
    household, reranker, monkeypatch
) -> None:
    """Even a reranker favoring every file must leave room for a replacement."""
    monkeypatch.setattr(get_settings().reading, "enabled", True)
    monkeypatch.setattr(get_settings().search, "files_in_five", 3)

    async def prefer_files(query: str, documents: list[str]) -> list[float]:
        """Score every marked file above every engram."""
        reranker.documents.extend(documents)
        return [100.0 if "FILEFIRST" in document else 0.0 for document in documents]

    monkeypatch.setattr(reranker, "score", prefer_files)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "car")
    await replace(me, "new.md", "old.md", "renewal")
    for number in range(6):
        await store_as(
            me,
            "personal",
            f"car-{number}.pdf",
            b"%PDF",
            "application/pdf",
            "car FILEFIRST",
        )
    hits = await search_engrams(
        me.principal, "car", limit=5, semantic=False, rerank=True
    )
    assert any("FILEFIRST" in document for document in reranker.documents)
    assert sum(hit["kind"] == "file" for hit in hits) == 3
    assert [hit["path"] for hit in hits if hit["kind"] == "page"] == [
        "new.md",
        "old.md",
    ]
