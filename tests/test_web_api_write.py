"""Write API: page saves with optimistic lock, space admin, authz."""

from conftest import _login

# Fixtures `api` and `world` live in tests/conftest.py, shared with
# test_web_api_read.py.

CSRF = {"X-Rif-Csrf": "1"}


async def test_put_creates_then_conflicts(api, world):
    """A first PUT creates version 1; a stale expected_version 409s."""
    alice, _bob, _ = world
    _login(api, alice)
    created = await api.put(
        "/api/pages/team/notes/a.md",
        json={"body": "First line.\n", "message": "create", "title": "A"},
        headers=CSRF,
    )
    assert created.status_code == 200
    assert created.json()["version"] == 1
    stale = await api.put(
        "/api/pages/team/notes/a.md",
        json={"body": "x", "message": "stale", "expected_version": 0},
        headers=CSRF,
    )
    assert stale.status_code == 409


async def test_put_without_csrf_header_is_403(api, world):
    """A PUT missing the CSRF header is rejected before it touches storage."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/team/notes/a.md", json={"body": "x", "message": "m"}
    )
    assert response.status_code == 403


async def test_put_missing_required_field_is_bad_request(api, world):
    """A PUT body missing the required ``message`` key 400s as bad_request."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/team/notes/a.md", json={"body": "x"}, headers=CSRF
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


async def test_meta_pages_are_read_only(api, world):
    """A generic PUT under meta/ is rejected as protected, not written."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/personal/meta/protocol.md",
        json={"body": "x", "message": "m"},
        headers=CSRF,
    )
    assert response.status_code == 403


async def test_create_space_and_slug_taken(api, world):
    """A space is created once; a repeat of the same slug 400s."""
    alice, _, _ = world
    _login(api, alice)
    ok = await api.post("/api/spaces", json={"slug": "trip"}, headers=CSRF)
    assert ok.status_code == 200
    assert ok.json() == {"alias": "trip", "slug": "trip"}
    dup = await api.post("/api/spaces", json={"slug": "trip"}, headers=CSRF)
    assert dup.status_code == 400


async def test_invite_and_members_and_remove(api, world):
    """Inviting adds a member, member listing reflects it, removal drops it."""
    alice, _bob, _ = world
    _login(api, alice)
    invited = await api.post(
        "/api/spaces/team/invites", json={"email": "New@X.com"}, headers=CSRF
    )
    assert invited.status_code == 200
    assert "disclosure" in invited.json()
    members = (await api.get("/api/spaces/team/members")).json()
    assert members["is_owner"] is True
    assert members["owner_email"] == alice.email
    assert len(members["members"]) == 3
    removed = await api.delete("/api/spaces/team/members/new@x.com", headers=CSRF)
    assert removed.status_code == 200


async def test_non_owner_cannot_invite(api, world):
    """A non-owner's invite attempt is rejected as a domain SpaceError."""
    _, bob, _ = world
    _login(api, bob)
    response = await api.post(
        "/api/spaces/team/invites", json={"email": "e@x.com"}, headers=CSRF
    )
    assert response.status_code == 400
