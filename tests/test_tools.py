from rif.access import Principal
from rif.pages import save_page
from rif.server import tool_load_context, tool_read_page


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_tool_load_context_serialises(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "household", "house.md", "boiler", message="x")
    result = await tool_load_context(session, me)
    assert result["truncated"] is False
    assert any(p["body"] == "boiler" for s in result["spaces"] for p in s["pages"])


async def test_tool_read_page_not_found_is_plain(session, household):
    me = principal_for(household["wouter"])
    assert (await tool_read_page(session, me, "personal", "nope.md"))["error"] == "not_found"
