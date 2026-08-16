"""Full-text search over pages, inside the same RLS session as every read.

The competitive positioning this feature carries: a search that forgets a
filter returns nothing, not somebody else's memories. The security tests
here are the point, not an afterthought.
"""

import pytest

from rif.access import AccessDenied, Principal
from rif.pages import save_page
from rif.search import search_pages


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_finds_a_page_by_words_in_its_body(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "house.md",
        "The boiler is a Vaillant ecoTEC and was serviced in March.",
        message="x",
        title="House",
    )
    results = await search_pages(me, "vaillant boiler")
    assert [r["path"] for r in results] == ["house.md"]
    assert results[0]["space"] == "personal"
    assert results[0]["title"] == "House"


async def test_snippet_bolds_the_matched_words(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "house.md",
        "A long preamble about the garden. The boiler is a Vaillant. "
        "More text about gutters follows here.",
        message="x",
    )
    results = await search_pages(me, "boiler")
    assert "**boiler**" in results[0]["snippet"]


async def test_searches_every_accessible_space_and_labels_by_alias(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    await save_page(me, "household", "house.md", "The boiler is new.", message="x")
    results = await search_pages(me, "boiler")
    assert {(r["space"], r["path"]) for r in results} == {
        ("personal", "notes.md"),
        ("household", "house.md"),
    }


async def test_title_match_outranks_body_match(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "mentions.md",
        "The insurance paperwork mentions the boiler once.",
        message="x",
    )
    await save_page(
        me,
        "personal",
        "boiler.md",
        "Service history and serial numbers.",
        message="x",
        title="Boiler",
    )
    results = await search_pages(me, "boiler")
    assert [r["path"] for r in results] == ["boiler.md", "mentions.md"]


async def test_scoping_to_one_space_excludes_the_others(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    await save_page(me, "household", "house.md", "The boiler is new.", message="x")
    results = await search_pages(me, "boiler", space="household")
    assert [(r["space"], r["path"]) for r in results] == [("household", "house.md")]


async def test_no_match_returns_an_empty_list(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "Nothing relevant.", message="x")
    assert await search_pages(me, "submarine") == []


async def test_blank_query_returns_an_empty_list(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    assert await search_pages(me, "   ") == []


async def test_unknown_space_is_denied_like_any_read(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(AccessDenied):
        await search_pages(me, "boiler", space="not-mine")


async def test_search_cannot_see_another_persons_personal_pages(tx, household):
    """The raw SQL walks the whole pages table; RLS must be what scopes it.

    The positive control (the owner finds their own page) proves the test
    would catch a policy that hid everything, not just one that leaked.
    """
    partner = principal_for(household["partner"])
    await save_page(
        partner, "personal", "diary.md", "The xylophone lessons resume.", message="x"
    )
    assert await search_pages(partner, "xylophone") != []
    me = principal_for(household["wouter"])
    assert await search_pages(me, "xylophone") == []


async def test_shared_space_matches_reach_both_members(tx, household):
    partner = principal_for(household["partner"])
    await save_page(
        partner, "household", "house.md", "The xylophone lives here.", message="x"
    )
    me = principal_for(household["wouter"])
    results = await search_pages(me, "xylophone")
    assert [(r["space"], r["path"]) for r in results] == [("household", "house.md")]


async def test_query_syntax_garbage_cannot_error(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    for garbage in ["boiler AND (", '"unclosed phrase', "a OR OR b", "-"]:
        await search_pages(me, garbage)
