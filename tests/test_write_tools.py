from rif.access import Principal
from rif.pages import get_page
from rif.server import tool_remember


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_remember_defaults_to_personal(session, household):
    me = principal_for(household["wouter"])
    await tool_remember(session, me, "the boiler is a Vaillant")
    assert await get_page(session, me, "household", "inbox.md") is None
    assert "Vaillant" in (await get_page(session, me, "personal", "inbox.md")).body


async def test_remember_appends(session, household):
    me = principal_for(household["wouter"])
    await tool_remember(session, me, "first fact")
    await tool_remember(session, me, "second fact")
    body = (await get_page(session, me, "personal", "inbox.md")).body
    assert "first fact" in body and "second fact" in body


async def test_remember_retry_does_not_duplicate(session, household):
    me = principal_for(household["wouter"])
    first = await tool_remember(session, me, "bin day is Tuesday")
    second = await tool_remember(session, me, "bin day is Tuesday")
    assert second["duplicate"] is True
    body = (await get_page(session, me, "personal", "inbox.md")).body
    assert body.count("bin day is Tuesday") == 1
    assert first["duplicate"] is False


async def test_remember_can_target_household_explicitly(session, household):
    me = principal_for(household["wouter"])
    await tool_remember(session, me, "school run swaps to me on Fridays",
                        space="household")
    assert "Fridays" in (await get_page(session, me, "household", "inbox.md")).body
