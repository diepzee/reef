"""Search methods tolerate spaces that disappear before their hits are read."""

import pytest
from conftest import caller_for, save_as

from reef.methods import find


@pytest.mark.parametrize("surviving", [False, True])
async def test_brief_skips_a_space_deleted_after_search(
    household, monkeypatch, seed, surviving
):
    """A deleted space is omitted while other hits remain usable."""
    me = caller_for(household["wouter"])
    spaces = ["household", "personal"] if surviving else ["household"]
    for space in spaces:
        await save_as(me, space, "boiler.md", "Boiler servicing.")
    search = find.search_engrams

    async def search_then_delete(*args, **kwargs):
        """Delete the shared space after the real search returns its hits.

        :param args: the search arguments
        :param kwargs: the search options
        :returns: the hits before the deletion
        """
        hits = await search(*args, **kwargs)
        assert {hit["cove"] for hit in hits} == set(spaces)
        await seed.execute("DELETE FROM spaces WHERE id = $1", household["shared"].id)
        return hits

    monkeypatch.setattr(find, "search_engrams", search_then_delete)
    items = (await find.brief(me, "", "boiler"))["engrams"]
    assert [item["space"] for item in items] == (["personal"] if surviving else [])
