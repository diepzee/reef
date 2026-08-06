from rif.access import Principal
from rif.pages import get_page
from rif.server import tool_remember


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_remember_defaults_to_personal(tx, household):
    me = principal_for(household["wouter"])
    await tool_remember(me, "the boiler is a Vaillant")
    assert await get_page(me, "household", "inbox.md") is None
    assert "Vaillant" in (await get_page(me, "personal", "inbox.md")).body


async def test_remember_appends(tx, household):
    me = principal_for(household["wouter"])
    await tool_remember(me, "first fact")
    await tool_remember(me, "second fact")
    body = (await get_page(me, "personal", "inbox.md")).body
    assert "first fact" in body and "second fact" in body


async def test_remember_retry_does_not_duplicate(tx, household):
    me = principal_for(household["wouter"])
    first = await tool_remember(me, "bin day is Tuesday")
    second = await tool_remember(me, "bin day is Tuesday")
    assert second["duplicate"] is True
    body = (await get_page(me, "personal", "inbox.md")).body
    assert body.count("bin day is Tuesday") == 1
    assert first["duplicate"] is False


async def test_remember_can_target_household_explicitly(tx, household):
    me = principal_for(household["wouter"])
    await tool_remember(me, "school run swaps to me on Fridays", space="household")
    assert "Fridays" in (await get_page(me, "household", "inbox.md")).body
