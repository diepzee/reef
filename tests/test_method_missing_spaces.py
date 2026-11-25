"""Search methods tolerate spaces that disappear before their hits are read."""

import logging

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


@pytest.mark.parametrize("method", ["recall", "brief"])
@pytest.mark.parametrize("vanished", [False, True])
async def test_search_logs_only_the_number_of_skipped_hits(
    household, monkeypatch, seed, caplog, method, vanished
):
    """One count-only line describes skipped hits; ordinary searches log nothing."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "cottage.md", "Boiler at the cottage.")
    await save_as(me, "household", "servicing.md", "Boiler servicing.")
    await save_as(me, "personal", "home.md", "Boiler at home.")
    search = find.search_engrams

    async def search_then_remove(*args, **kwargs):
        """Optionally remove the shared space after the search.

        :param args: the search arguments
        :param kwargs: the search options
        :returns: the original three hits
        """
        hits = await search(*args, **kwargs)
        assert len(hits) == 3
        if vanished:
            await seed.execute(
                "DELETE FROM spaces WHERE id = $1", household["shared"].id
            )
        return hits

    monkeypatch.setattr(find, "search_engrams", search_then_remove)
    caplog.set_level(logging.INFO, logger=find.__name__)
    result = await getattr(find, method)(me, "A private reason.", "boiler")
    items = result["engrams"] if method == "brief" else result
    assert len(items) == (1 if vanished else 3)
    records = [record for record in caplog.records if record.name == find.__name__]
    assert [record.getMessage() for record in records] == (
        [f"{method} skipped 2 hits from unavailable spaces"] if vanished else []
    )
    assert all(record.args == (2,) for record in records)
