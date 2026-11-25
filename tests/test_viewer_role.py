"""The viewer role, surfaced: read everything, write nothing, said clearly.

Postgres has enforced ``role = 'member'`` on every content write since day
one; ``test_security`` proves that with hand-seeded rows. These tests cover
the application finally creating viewers on purpose — invites that grant
the role, and write paths that refuse with a message instead of a silent
zero-row update. That the methods refuse a viewer plainly is tested with
the methods.
"""

import pytest

from reef.access import Principal, ReadOnlyMembership
from reef.engrams import delete_engram, save_engram
from reef.methods import Caller, find
from reef.search import search_engrams
from reef.spaces import SpaceError, invite


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


@pytest.fixture
async def viewer(household, graph):
    """A third person holding a viewer membership in the shared space."""
    person = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_space(person, slug="viewer")
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
    with pytest.raises(SpaceError):
        await invite(me, "household", "x@example.test", role="admin")


async def test_a_viewer_reads_engrams_and_search(tx, household, viewer):
    me = principal_for(household["wouter"])
    await save_engram(me, "household", "house.md", "The boiler is new.", message="x")
    them = principal_for(viewer)
    caller = Caller(principal=them, writer="assistant", session_id=None)
    [engram] = await find.open(caller, "", ["household/house.md"])
    assert engram["body"] == "The boiler is new."
    assert await search_engrams(them, "boiler") != []


async def test_a_viewer_cannot_write_an_engram(tx, household, viewer):
    them = principal_for(viewer)
    with pytest.raises(ReadOnlyMembership):
        await save_engram(them, "household", "note.md", "Hi.", message="x")


async def test_a_viewer_cannot_delete_an_engram(tx, household, viewer):
    me = principal_for(household["wouter"])
    await save_engram(me, "household", "house.md", "The boiler is new.", message="x")
    them = principal_for(viewer)
    with pytest.raises(ReadOnlyMembership):
        await delete_engram(them, "household", "house.md")


async def test_a_viewer_still_writes_their_own_personal_space(tx, household, viewer):
    them = principal_for(viewer)
    engram = await save_engram(them, "personal", "mine.md", "My note.", message="x")
    assert engram.body == "My note."
