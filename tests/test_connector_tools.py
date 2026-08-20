"""The two-tool shape connectors expect: `search` and `fetch`.

Some clients will only ever call two tools. ChatGPT outside developer mode is
the clearest case — it restricts a connector to `search` and `fetch` and
ignores every other tool — and the same pair is what makes reef usable as a
company knowledge source.

These are adapters, not new capability: `search` is `search_pages` reshaped,
and `fetch` is `read_page`/`read_file` behind a single opaque id. They inherit
the access rules of what they wrap, which is the point — a connector shape
must not become a way around the boundary.
"""

from reef.access import Principal
from reef.pages import save_page
from reef.server import tool_fetch, tool_search


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_search_returns_the_connector_shape(tx, household):
    me = principal_for(household["wouter"])
    await save_page(
        me, "household", "boiler.md", "The boiler is a Vaillant.", message="x"
    )
    results = (await tool_search(me, "Vaillant"))["results"]
    assert results
    for result in results:
        assert set(result) == {"id", "title", "url"}


async def test_an_id_from_search_fetches_the_page(tx, household):
    """The contract between the two tools: search's id must resolve."""
    me = principal_for(household["wouter"])
    await save_page(
        me, "household", "boiler.md", "The boiler is a Vaillant.", message="x"
    )
    result = (await tool_search(me, "Vaillant"))["results"][0]
    fetched = await tool_fetch(me, result["id"])
    assert "Vaillant" in fetched["text"]
    assert fetched["id"] == result["id"]
    assert set(fetched) >= {"id", "title", "text", "url", "metadata"}


async def test_the_url_points_at_the_page_in_the_app(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "boiler.md", "Vaillant.", message="x")
    result = (await tool_search(me, "Vaillant"))["results"][0]
    assert result["url"].endswith("/app/s/household/p/boiler.md")


async def test_fetch_refuses_an_unresolvable_id(tx, household):
    me = principal_for(household["wouter"])
    assert (await tool_fetch(me, "page:household/nope.md"))["error"] == "not_found"


async def test_fetch_refuses_a_malformed_id(tx, household):
    me = principal_for(household["wouter"])
    assert (await tool_fetch(me, "nonsense"))["error"] == "bad_id"


async def test_search_cannot_see_another_persons_space(tx, household):
    """The adapter inherits the boundary; it does not get to widen it."""
    partner = principal_for(household["partner"])
    wouter = principal_for(household["wouter"])
    await save_page(
        wouter, "personal", "secret.md", "Vaillant boiler secret.", message="x"
    )
    results = (await tool_search(partner, "Vaillant"))["results"]
    assert all("secret.md" not in r["id"] for r in results)


async def test_fetch_refuses_a_page_the_caller_cannot_read(tx, household):
    """An id is opaque, not a capability. Guessing one must not work."""
    wouter = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await save_page(wouter, "personal", "secret.md", "Private.", message="x")
    assert (await tool_fetch(partner, "page:personal/secret.md"))[
        "error"
    ] == "not_found"
