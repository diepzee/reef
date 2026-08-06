from rif.access import Principal
from rif.pages import save_page
from rif.server import tool_list_spaces, tool_load_context, tool_read_page


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_tool_load_context_serialises(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "house.md", "boiler", message="x")
    result = await tool_load_context(me)
    assert result["truncated"] is False
    assert any(p["body"] == "boiler" for s in result["spaces"] for p in s["pages"])


async def test_tool_read_page_not_found_is_plain(tx, household):
    me = principal_for(household["wouter"])
    assert (await tool_read_page(me, "personal", "nope.md"))["error"] == "not_found"


async def test_tool_list_spaces_returns_alias_not_slug(tx, household):
    me = principal_for(household["wouter"])
    result = await tool_list_spaces(me)
    assert {row["alias"] for row in result} == {"personal", "household"}
    for row in result:
        assert set(row.keys()) == {"alias", "version"}
        assert "slug" not in row
        assert household["shared"].slug not in row.values()


async def test_tool_load_index_serialises(tx, household):
    from rif.server import tool_load_index

    me = principal_for(household["wouter"])
    await save_page(
        me, "household", "house.md", "The family home.\n\nDetail.", message="x"
    )
    result = await tool_load_index(me)
    pages = [p for s in result["spaces"] for p in s["pages"]]
    assert pages and pages[0]["description"] == "The family home."
    assert all("body" not in p for p in pages)


async def test_tool_read_pages_fetches_batch_with_not_found_markers(tx, household):
    from rif.server import tool_read_pages

    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "alpha", message="x")
    await save_page(me, "personal", "b.md", "beta", message="x")

    results = await tool_read_pages(me, "personal", ["a.md", "nope.md", "b.md"])

    assert [r.get("body", r.get("error")) for r in results] == [
        "alpha",
        "not_found",
        "beta",
    ]
