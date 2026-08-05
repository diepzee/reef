from sqlalchemy import select, text

from rif.access import Principal, resolve_space
from rif.models import Page, Revision


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def _seed_private_page(session, household) -> Page:
    await resolve_space(session, principal_for(household["wouter"]), "personal")
    page = Page(space_id=household["w_personal"].id, path="health.md",
                title="Health", body="private medical detail")
    session.add(page)
    await session.flush()
    session.add(Revision(page_id=page.id, path=page.path, title=page.title,
                         tags=[], body=page.body, message="seed",
                         author_id=household["wouter"].id))
    await session.flush()
    return page


async def test_raw_select_as_other_principal_returns_nothing(session, household):
    await _seed_private_page(session, household)
    await resolve_space(session, principal_for(household["partner"]), "personal")
    assert (await session.scalars(select(Page).where(Page.path == "health.md"))).all() == []


async def test_raw_select_on_revisions_is_also_denied(session, household):
    await _seed_private_page(session, household)
    await resolve_space(session, principal_for(household["partner"]), "personal")
    assert (await session.scalars(select(Revision))).all() == []


async def test_forged_insert_into_foreign_space_is_rejected(session, household):
    import pytest
    from sqlalchemy.exc import ProgrammingError

    await resolve_space(session, principal_for(household["partner"]), "personal")
    session.add(Page(space_id=household["w_personal"].id, path="planted.md",
                     title="x", body="forged"))
    with pytest.raises(ProgrammingError):
        await session.flush()


async def test_no_principal_means_no_rows_even_after_seeding(session, household):
    await _seed_private_page(session, household)
    await session.execute(text("SELECT set_config('app.person_id', '', true)"))
    assert (await session.scalars(select(Page))).all() == []
