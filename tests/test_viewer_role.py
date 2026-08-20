"""The viewer role, surfaced: read everything, write nothing, said clearly.

Postgres has enforced ``role = 'member'`` on every content write since day
one; ``test_security`` proves that with hand-seeded rows. These tests cover
the application finally creating viewers on purpose — invites that grant
the role, write paths that refuse with a message instead of a silent
zero-row update, and rosters that say who can only read.
"""

import pytest

from reef.access import Principal, ReadOnlyMembership
from reef.coves import CoveError, invite
from reef.pages import delete_page, edit_section, save_page
from reef.search import search_pages
from reef.server import tool_list_coves, tool_read_page, tool_remember


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


@pytest.fixture
async def viewer(household, graph):
    """A third person holding a viewer membership in the shared cove."""
    person = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_cove(person, slug="viewer")
    await graph.add_membership(person, household["shared"], "viewer", alias="household")
    return person


async def test_invite_can_grant_the_viewer_role(tx, household, graph):
    me = principal_for(household["wouter"])
    result = await invite(me, "household", "reader@example.test", role="viewer")
    assert result["role"] == "viewer"
    assert "read" in result["disclosure"].lower()
    assert "write" in result["disclosure"].lower()


async def test_invite_defaults_to_full_membership(tx, household):
    me = principal_for(household["wouter"])
    result = await invite(me, "household", "full@example.test")
    assert result["role"] == "member"


async def test_invite_refuses_an_unknown_role(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(CoveError):
        await invite(me, "household", "x@example.test", role="admin")


async def test_a_viewer_reads_pages_and_search(tx, household, viewer):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "house.md", "The boiler is new.", message="x")
    them = principal_for(viewer)
    page = await tool_read_page(them, "household", "house.md")
    assert page["body"] == "The boiler is new."
    assert await search_pages(them, "boiler") != []


async def test_a_viewer_cannot_write_a_page(tx, household, viewer):
    them = principal_for(viewer)
    with pytest.raises(ReadOnlyMembership):
        await save_page(them, "household", "note.md", "Hi.", message="x")


async def test_a_viewer_cannot_edit_a_section(tx, household, viewer):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "house.md", "The boiler is new.", message="x")
    them = principal_for(viewer)
    with pytest.raises(ReadOnlyMembership):
        await edit_section(them, "household", "house.md", "new", "old", message="x")


async def test_a_viewer_cannot_delete_a_page(tx, household, viewer):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "house.md", "The boiler is new.", message="x")
    them = principal_for(viewer)
    with pytest.raises(ReadOnlyMembership):
        await delete_page(them, "household", "house.md")


async def test_a_viewer_cannot_remember_into_the_cove(tx, household, viewer):
    them = principal_for(viewer)
    with pytest.raises(ReadOnlyMembership):
        await tool_remember(them, "the boiler is new", "household")


async def test_a_viewer_still_writes_their_own_personal_cove(tx, household, viewer):
    them = principal_for(viewer)
    page = await save_page(them, "personal", "mine.md", "My note.", message="x")
    assert page.body == "My note."


async def test_list_coves_says_who_can_only_read(tx, household, viewer):
    me = principal_for(household["wouter"])
    rows = {row["name"]: row for row in await tool_list_coves(me)}
    assert rows["household"]["viewers"] == ["Viewer"]
    assert rows["personal"]["viewers"] == []
    assert "Viewer" in rows["household"]["members"]
