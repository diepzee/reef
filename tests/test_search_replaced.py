"""Replacement ranking stays within the reader's view of the graph."""

from uuid import uuid4

from conftest import caller_for, save_as, store_as

from reef import search
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


async def test_twenty_queries_without_replacements_are_unchanged(
    household, monkeypatch
) -> None:
    """The rank adjustment is exactly inert for memory without replaces."""
    me = caller_for(household["wouter"])
    for number in range(20):
        await save_as(
            me, "personal", f"{number}.md", f"contract topic{number} terms agreement"
        )
    for number in range(20):
        query = f"contract topic{number}"
        actual = await search_engrams(me.principal, query, semantic=False, rerank=False)
        with monkeypatch.context() as patch:
            patch.setattr(
                search, "demote", lambda ranking, replaced, insertable: ranking
            )
            original = await search_engrams(
                me.principal, query, semantic=False, rerank=False
            )
        assert actual == original
