from sqlalchemy import select, text

from rif.models import Membership, Page


async def test_household_fixture_creates_four_memberships(session, household):
    assert len((await session.scalars(select(Membership))).all()) == 4


async def test_content_is_invisible_without_a_principal(session, household):
    await session.execute(text(
        "SELECT set_config('app.person_id', :pid, true)"),
        {"pid": str(household["wouter"].id)})
    session.add(Page(space_id=household["w_personal"].id, path="a.md",
                     title="a", body="secret"))
    await session.flush()
    await session.execute(text("SELECT set_config('app.person_id', '', true)"))
    assert (await session.scalars(select(Page))).all() == []
