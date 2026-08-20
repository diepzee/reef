"""Path normalization on write, and page deletion."""

import pytest

from reef.access import Principal
from reef.models import Page, Revision
from reef.pages import (
    InvalidPath,
    PageNotFound,
    ProtectedPath,
    delete_page,
    get_page,
    normalize_path,
    save_page,
)


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


@pytest.mark.parametrize(
    ("asked", "stored"),
    [
        ("notes/first-day", "notes/first-day.md"),
        ("notes/first-day.md", "notes/first-day.md"),
        ("  Trip/Packing List  ", "trip/packing-list.md"),
        ("NOTES/UPPER.MD", "notes/upper.md"),
    ],
)
def test_mechanical_problems_are_repaired(asked, stored):
    """Case, whitespace, and the missing extension are fixed, not refused."""
    assert normalize_path(asked) == stored


@pytest.mark.parametrize(
    "asked",
    [
        "",
        "   ",
        "/leading.md",
        "notes//gap.md",
        "../escape.md",
        "notes/what?.md",
        ".md",
    ],
)
def test_unrepairable_paths_raise(asked):
    """What cannot be guessed at is refused, with the reason in the message."""
    with pytest.raises(InvalidPath):
        normalize_path(asked)


async def test_a_new_page_is_stored_under_its_tidy_name(tx, household):
    """The MCP surface now names pages the way the web form does."""
    me = principal_for(household["wouter"])
    page = await save_page(me, "personal", "Notes/First Day", "hi", message="x")
    assert page.path == "notes/first-day.md"
    assert await get_page(me, "personal", "notes/first-day.md") is not None


async def test_a_legacy_path_stays_writable_under_its_own_name(tx, household):
    """An existing odd path is addressed exactly as stored, never duplicated.

    Paths were unconstrained before ``normalize_path``, so the corpus can
    hold ``notes/UPPER.MD``. Normalizing on every write would not rename it
    -- it would write ``notes/upper.md`` beside it and strand the original.
    """
    me = principal_for(household["wouter"])
    created = await save_page(me, "personal", "keep.md", "v1", message="x")
    await Page.update({Page.path: "notes/UPPER.MD"}).where(Page.id == created.id)

    updated = await save_page(me, "personal", "notes/UPPER.MD", "v2", message="y")

    assert updated.path == "notes/UPPER.MD"
    assert updated.version == 2
    assert await get_page(me, "personal", "notes/upper.md") is None
    assert len(await Page.objects().where(Page.cove_id == created.cove_id)) == 1


async def test_writing_an_unrepairable_path_raises(tx, household):
    """The refusal reaches the caller rather than storing junk."""
    me = principal_for(household["wouter"])
    with pytest.raises(InvalidPath):
        await save_page(me, "personal", "../escape.md", "nope", message="x")


async def test_normalizing_cannot_smuggle_a_write_into_meta(tx, household):
    """``META/Persona`` is protected even though the raw path is not lowercase."""
    me = principal_for(household["wouter"])
    with pytest.raises(ProtectedPath):
        await save_page(me, "personal", "META/Persona", "nope", message="x")


async def test_delete_removes_the_page_and_its_history(tx, household):
    """The whole point: a mistyped page can be taken back out again."""
    me = principal_for(household["wouter"])
    page = await save_page(me, "personal", "typo.md", "v1", message="x")
    await save_page(me, "personal", "typo.md", "v2", message="y", expected_version=1)

    outcome = await delete_page(me, "personal", "typo.md")

    assert outcome == {"deleted": True, "path": "typo.md", "revisions": 2}
    assert await get_page(me, "personal", "typo.md") is None
    assert await Revision.count().where(Revision.page_id == page.id) == 0


async def test_deleting_a_missing_page_is_refused(tx, household):
    """A path that names nothing is an error, not a silent success."""
    me = principal_for(household["wouter"])
    with pytest.raises(PageNotFound):
        await delete_page(me, "personal", "never-existed.md")


async def test_the_persona_page_cannot_be_deleted(tx, household):
    """meta/ is machinery; a cove without it is not a state reef expects."""
    me = principal_for(household["wouter"])
    await save_page(
        me, "personal", "meta/persona.md", "who", message="x", allow_protected=True
    )
    with pytest.raises(ProtectedPath):
        await delete_page(me, "personal", "meta/persona.md")
    assert await get_page(me, "personal", "meta/persona.md") is not None


async def test_delete_is_scoped_to_the_cove_you_named(tx, household):
    """A page of the same name elsewhere is untouched."""
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "same.md", "mine", message="x")
    await save_page(me, "household", "same.md", "ours", message="x")

    await delete_page(me, "personal", "same.md")

    assert await get_page(me, "personal", "same.md") is None
    assert (await get_page(me, "household", "same.md")).body == "ours"


async def test_a_deleted_page_leaves_the_cove_version_bumped(tx, household):
    """Deleting changes the corpus, so a cached index must be invalidated."""
    me = principal_for(household["wouter"])
    page = await save_page(me, "personal", "gone.md", "x", message="x")
    from reef.models import Cove

    before = (await Cove.objects().where(Cove.id == page.cove_id).first()).version
    await delete_page(me, "personal", "gone.md")
    after = (await Cove.objects().where(Cove.id == page.cove_id).first()).version
    assert after > before
