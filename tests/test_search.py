"""Full-text search over pages, inside the same RLS session as every read.

The competitive positioning this feature carries: a search that forgets a
filter returns nothing, not somebody else's memories. The security tests
here are the point, not an afterthought.
"""

import pytest

from reef.access import AccessDenied, Principal, arm
from reef.models import Attachment, AttachmentStatus
from reef.pages import save_page
from reef.search import search_pages


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def _add_file(principal, cove, key, filename, description, status=None):
    await arm(principal)
    await Attachment(
        cove_id=cove.id,
        object_key=key,
        filename=filename,
        mime="application/pdf",
        byte_size=1,
        description=description,
        status=(status or AttachmentStatus.READY).value,
    ).save()


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
    assert results[0]["cove"] == "personal"
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


async def test_searches_every_accessible_cove_and_labels_by_alias(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    await save_page(me, "household", "house.md", "The boiler is new.", message="x")
    results = await search_pages(me, "boiler")
    assert {(r["cove"], r["path"]) for r in results} == {
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


async def test_scoping_to_one_cove_excludes_the_others(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    await save_page(me, "household", "house.md", "The boiler is new.", message="x")
    results = await search_pages(me, "boiler", cove="household")
    assert [(r["cove"], r["path"]) for r in results] == [("household", "house.md")]


async def test_no_match_returns_an_empty_list(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "Nothing relevant.", message="x")
    assert await search_pages(me, "submarine") == []


async def test_blank_query_returns_an_empty_list(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    assert await search_pages(me, "   ") == []


async def test_unknown_cove_is_denied_like_any_read(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(AccessDenied):
        await search_pages(me, "boiler", cove="not-mine")


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


async def test_shared_cove_matches_reach_both_members(tx, household):
    partner = principal_for(household["partner"])
    await save_page(
        partner, "household", "house.md", "The xylophone lives here.", message="x"
    )
    me = principal_for(household["wouter"])
    results = await search_pages(me, "xylophone")
    assert [(r["cove"], r["path"]) for r in results] == [("household", "house.md")]


async def test_finds_a_file_by_its_description(tx, household):
    me = principal_for(household["wouter"])
    await _add_file(
        me,
        household["w_personal"],
        "attachments/lease",
        "lease.pdf",
        "Signed rental agreement for the flat.",
    )
    results = await search_pages(me, "rental agreement")
    assert len(results) == 1
    hit = results[0]
    assert hit["kind"] == "file"
    assert hit["cove"] == "personal"
    assert hit["key"] == "attachments/lease"
    assert hit["filename"] == "lease.pdf"
    assert "**rental**" in hit["snippet"]


async def test_page_results_say_they_are_pages(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    results = await search_pages(me, "boiler")
    assert results[0]["kind"] == "page"


async def test_a_file_whose_bytes_never_landed_is_not_searchable(tx, household):
    me = principal_for(household["wouter"])
    await _add_file(
        me,
        household["w_personal"],
        "attachments/ghost",
        "ghost.pdf",
        "A xylophone maintenance guide.",
        status=AttachmentStatus.PENDING,
    )
    assert await search_pages(me, "xylophone") == []


async def test_search_cannot_see_another_persons_files(tx, household):
    partner = principal_for(household["partner"])
    await _add_file(
        partner,
        household["p_personal"],
        "attachments/private",
        "private.pdf",
        "A xylophone recital programme.",
    )
    assert await search_pages(partner, "xylophone") != []
    me = principal_for(household["wouter"])
    assert await search_pages(me, "xylophone") == []


async def test_query_syntax_garbage_cannot_error(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "The boiler manual.", message="x")
    for garbage in ["boiler AND (", '"unclosed phrase', "a OR OR b", "-"]:
        await search_pages(me, garbage)
