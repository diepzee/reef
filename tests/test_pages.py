import pytest
from sqlalchemy import select

from rif.access import Principal
from rif.models import Revision, Space
from rif.pages import (
    ProtectedPath,
    SectionNotFound,
    VersionConflict,
    edit_section,
    get_page,
    save_page,
)


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_save_then_get_round_trips(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "health.md", "# Health\n\nSleeps badly.",
                    message="initial", title="Health", tags=["person"])
    page = await get_page(session, me, "personal", "health.md")
    assert page.title == "Health" and "Sleeps badly" in page.body


async def test_each_write_snapshots_full_state_and_bumps_versions(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "a.md", "one", message="first",
                    title="A", tags=["t"])
    await save_page(session, me, "personal", "a.md", "two", message="second")
    revisions = (await session.scalars(select(Revision).order_by(Revision.created_at))).all()
    assert [r.body for r in revisions] == ["one", "two"]
    assert revisions[1].title == "A" and revisions[1].tags == ["t"]
    page = await get_page(session, me, "personal", "a.md")
    assert page.version == 2
    space = await session.get(Space, household["w_personal"].id)
    assert space.version == 2


async def test_stale_expected_version_conflicts(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "a.md", "one", message="x")
    with pytest.raises(VersionConflict):
        await save_page(session, me, "personal", "a.md", "clobber",
                        message="x", expected_version=0)


async def test_one_persons_page_is_invisible_to_the_other(session, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(session, mine, "personal", "secret.md", "mine", message="x")
    assert await get_page(session, theirs, "personal", "secret.md") is None


async def test_household_pages_are_visible_to_both(session, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(session, mine, "household", "house.md", "boiler", message="x")
    assert (await get_page(session, theirs, "household", "house.md")).body == "boiler"


async def test_edit_section_replaces_exactly_one_occurrence(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "household", "house.md",
                    "Boiler: Vaillant\nRoof: tiles", message="x")
    page = await edit_section(session, me, "household", "house.md",
                              "Boiler: Vaillant", "Boiler: Vaillant ecoTEC",
                              message="model number")
    assert page.body == "Boiler: Vaillant ecoTEC\nRoof: tiles"
    with pytest.raises(SectionNotFound):
        await edit_section(session, me, "household", "house.md",
                           "Chimney", "Chimney: swept", message="x")


async def test_meta_paths_are_protected_from_generic_writes(session, household):
    me = principal_for(household["wouter"])
    with pytest.raises(ProtectedPath):
        await save_page(session, me, "personal", "meta/persona.md", "hijacked",
                        message="x")
    page = await save_page(session, me, "personal", "meta/persona.md", "legit",
                           message="x", allow_protected=True)
    assert page.body == "legit"
