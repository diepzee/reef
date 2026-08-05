from rif.access import Principal
from rif.pages import save_page
from rif.server import tool_list_spaces, tool_load_context, tool_read_page


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


async def test_tool_list_spaces_returns_alias_not_slug(session, household):
    me = principal_for(household["wouter"])
    result = await tool_list_spaces(session, me)
    assert {row["alias"] for row in result} == {"personal", "household"}
    for row in result:
        assert set(row.keys()) == {"alias", "version"}
        assert "slug" not in row
        assert household["shared"].slug not in row.values()
