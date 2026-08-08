"""Read API: membership slicing, page fetch, 401s."""

from conftest import _login

from rif.db import transaction_scope
from rif.pages import save_page

# Fixtures `api` and `world` live in tests/conftest.py, shared with
# test_web_api_write.py.


async def test_unauthenticated_index_is_401(api):
    """An unauthenticated request to /api/index is rejected with 401."""
    response = await api.get("/api/index")
    assert response.status_code == 401


async def test_index_is_sliced_per_person(api, world):
    """The index only lists spaces the logged-in person is a member of."""
    _alice, bob, _ = world
    _login(api, bob)
    response = await api.get("/api/index")
    assert response.status_code == 200
    aliases = {space["alias"] for space in response.json()["spaces"]}
    assert aliases == {"personal", "team"}


async def test_me(api, world):
    """/api/me reports the logged-in person's identity."""
    alice, _, _ = world
    _login(api, alice)
    body = (await api.get("/api/me")).json()
    assert body["email"] == "alice@x.com"
    assert body["display_name"] == "Alice"


async def test_get_page_and_404(api, world):
    """A page fetch returns the body; missing paths and foreign spaces 404."""
    from rif.access import Principal

    alice, bob, _ = world
    async with transaction_scope():
        await save_page(
            Principal(person_id=alice.id, email=alice.email),
            "team",
            "notes/plan.md",
            "# Plan\n\nThe plan summary.\n",
            message="seed",
        )
    _login(api, bob)
    page = (await api.get("/api/pages/team/notes/plan.md")).json()
    assert page["body"].startswith("# Plan")
    assert page["version"] == 1
    missing = await api.get("/api/pages/team/notes/absent.md")
    assert missing.status_code == 404
    foreign = await api.get("/api/pages/other-space/notes/plan.md")
    assert foreign.status_code == 404
