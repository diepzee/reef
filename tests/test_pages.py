import pytest

from reef.access import Principal
from reef.config import get_settings
from reef.models import Cove, Revision
from reef.pages import (
    PageTooLarge,
    ProtectedPath,
    SectionNotFound,
    VersionConflict,
    edit_section,
    get_page,
    save_page,
)


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_save_then_get_round_trips(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me,
        "personal",
        "health.md",
        "# Health\n\nSleeps badly.",
        message="initial",
        title="Health",
        tags=["person"],
    )
    page = await get_page(me, "personal", "health.md")
    assert page.title == "Health" and "Sleeps badly" in page.body


async def test_each_write_snapshots_full_state_and_bumps_versions(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me, "personal", "a.md", "one", message="first", title="A", tags=["t"]
    )
    await save_page(me, "personal", "a.md", "two", message="second")
    revisions = await Revision.objects().order_by(Revision.created_at)
    assert [r.body for r in revisions] == ["one", "two"]
    assert revisions[1].title == "A" and revisions[1].tags == ["t"]
    page = await get_page(me, "personal", "a.md")
    assert page.version == 2
    cove = await Cove.objects().where(Cove.id == household["w_personal"].id).first()
    assert cove.version == 2


async def test_stale_expected_version_conflicts(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "one", message="x")
    with pytest.raises(VersionConflict):
        await save_page(
            me, "personal", "a.md", "clobber", message="x", expected_version=0
        )


async def test_one_persons_page_is_invisible_to_the_other(tx, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(mine, "personal", "secret.md", "mine", message="x")
    assert await get_page(theirs, "personal", "secret.md") is None


async def test_household_pages_are_visible_to_both(tx, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(mine, "household", "house.md", "boiler", message="x")
    assert (await get_page(theirs, "household", "house.md")).body == "boiler"


async def test_edit_section_replaces_exactly_one_occurrence(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me, "household", "house.md", "Boiler: Vaillant\nRoof: tiles", message="x"
    )
    page = await edit_section(
        me,
        "household",
        "house.md",
        "Boiler: Vaillant",
        "Boiler: Vaillant ecoTEC",
        message="model number",
    )
    assert page.body == "Boiler: Vaillant ecoTEC\nRoof: tiles"
    with pytest.raises(SectionNotFound):
        await edit_section(
            me, "household", "house.md", "Chimney", "Chimney: swept", message="x"
        )


async def test_meta_paths_are_protected_from_generic_writes(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(ProtectedPath):
        await save_page(me, "personal", "meta/persona.md", "hijacked", message="x")
    page = await save_page(
        me, "personal", "meta/persona.md", "legit", message="x", allow_protected=True
    )
    assert page.body == "legit"


async def test_a_body_over_the_ceiling_is_refused(tx, household):
    """Pages had no size limit while files were capped at 25 MB, so prose was
    the cheapest way to bloat a cove -- and build_index reads every body on
    every call, so one oversized page taxes every member's every turn."""
    me = principal_for(household["wouter"])
    ceiling = get_settings().page_max_chars
    with pytest.raises(PageTooLarge):
        await save_page(me, "household", "bloat.md", "A" * (ceiling + 1), message="x")
    assert await get_page(me, "household", "bloat.md") is None


async def test_a_body_at_the_ceiling_is_allowed(tx, household):
    me = principal_for(household["wouter"])
    ceiling = get_settings().page_max_chars
    page = await save_page(me, "household", "big.md", "A" * ceiling, message="x")
    assert len(page.body) == ceiling
