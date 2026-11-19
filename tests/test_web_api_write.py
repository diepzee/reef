"""Write API: page saves with optimistic lock, cove admin, authz."""

from conftest import _login

from reef.invitations import INVITE_BUDGET

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
    assert created.json()["last_editor"] == "Alice"
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


async def test_create_cove_and_slug_taken(api, world):
    """A cove is created once; a repeat of the same slug 400s."""
    alice, _, _ = world
    _login(api, alice)
    ok = await api.post("/api/coves", json={"slug": "trip"}, headers=CSRF)
    assert ok.status_code == 200
    assert ok.json() == {"alias": "trip", "slug": "trip"}
    dup = await api.post("/api/coves", json={"slug": "trip"}, headers=CSRF)
    assert dup.status_code == 400


async def test_invite_and_members_and_remove(api, world):
    """Inviting adds a member, member listing reflects it, removal drops it."""
    alice, bob, _ = world
    _login(api, alice)
    invited = await api.post(
        "/api/coves/team/invites", json={"email": "New@X.com"}, headers=CSRF
    )
    assert invited.status_code == 200
    assert "disclosure" in invited.json()
    members = (await api.get("/api/coves/team/members")).json()
    assert members["is_owner"] is True
    assert members["owner_email"] == alice.email
    assert len(members["members"]) == 3
    assert all(
        set(member) == {"person_id", "display_name", "email", "avatar"}
        for member in members["members"]
    )
    # Nobody in this cove has chosen a picture, so every row says so rather
    # than pointing at a URL that would 404 -- that is what makes the UI draw
    # an initial instead of asking.
    assert all(member["avatar"] is None for member in members["members"])
    assert {member["email"] for member in members["members"]} == {
        alice.email,
        bob.email,
        "new@x.com",
    }

    # A logged-in response renews the session cookie without an explicit
    # domain, and httpx's cookie jar then holds that renewed cookie
    # alongside whatever _login sets next under a distinct (bare) domain --
    # both match the request and get sent together, so the switch to bob
    # must clear the jar first or the request would carry alice's cookie too.
    api.cookies.clear()
    _login(api, bob)
    non_owner_view = (await api.get("/api/coves/team/members")).json()
    assert non_owner_view["is_owner"] is False
    assert {m["display_name"] for m in non_owner_view["members"]} == {
        m["display_name"] for m in members["members"]
    }
    assert all(m["email"] == "" for m in non_owner_view["members"])

    api.cookies.clear()
    _login(api, alice)
    removed = await api.delete("/api/coves/team/members/new@x.com", headers=CSRF)
    assert removed.status_code == 200


async def test_non_owner_cannot_invite(api, world):
    """A non-owner's invite attempt is rejected as a domain CoveError."""
    _, bob, _ = world
    _login(api, bob)
    response = await api.post(
        "/api/coves/team/invites", json={"email": "e@x.com"}, headers=CSRF
    )
    assert response.status_code == 400


async def test_put_non_dict_json_body_is_bad_request(api, world):
    """A JSON body that parses to a bare int, not an object, 400s cleanly.

    Regression test: this used to reach ``if "body" not in payload`` with an
    int, raising an unhandled ``TypeError`` ("argument of type 'int' is not
    iterable") instead of a clean 400.
    """
    alice, _, _ = world
    _login(api, alice)
    response = await api.put("/api/pages/team/notes/a.md", json=42, headers=CSRF)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


async def test_put_malformed_json_is_bad_request(api, world):
    """Syntactically invalid JSON 400s instead of raising a decode error."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/team/notes/a.md",
        content=b"{not valid json",
        headers={**CSRF, "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


async def test_put_wrong_typed_body_is_bad_request(api, world):
    """A non-string ``body`` 400s instead of reaching the database driver.

    Regression test: this used to reach ``save_page`` and die inside
    asyncpg with a ``DataError`` (expected str, got int) instead of a clean
    400.
    """
    alice, _, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/team/notes/a.md",
        json={"body": 42, "message": "m"},
        headers=CSRF,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


async def test_put_wrong_typed_tags_is_bad_request(api, world):
    """A ``tags`` field that is a string, not a list, 400s cleanly."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/team/notes/a.md",
        json={"body": "x", "message": "m", "tags": "not-a-list"},
        headers=CSRF,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


async def test_post_coves_non_dict_json_body_is_bad_request(api, world):
    """A JSON body that parses to a bare list, not an object, 400s cleanly."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.post("/api/coves", json=["trip"], headers=CSRF)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


async def test_invite_non_dict_json_body_is_bad_request(api, world):
    """A JSON body that parses to a bare string, not an object, 400s cleanly."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.post("/api/coves/team/invites", json="e@x.com", headers=CSRF)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


async def test_invite_to_reef_allowlists_without_sharing_a_cove(api, world):
    """The reef invite grants no membership — that is its whole purpose."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.post(
        "/api/invites", json={"email": "Curious@X.com"}, headers=CSRF
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "curious@x.com"
    assert body["already_known"] is False
    assert body["invites_left"] == INVITE_BUDGET - 1
    assert "next_step" in body

    # They are on the allowlist, but in none of alice's coves.
    members = (await api.get("/api/coves/team/members")).json()
    assert "curious@x.com" not in {m["email"] for m in members["members"]}


async def test_invites_left_is_readable_before_spending_any(api, world):
    alice, _, _ = world
    _login(api, alice)
    body = (await api.get("/api/invites")).json()
    assert body["invites_left"] == INVITE_BUDGET
    assert body["budget"] == INVITE_BUDGET


async def test_spent_budget_returns_429_with_the_unlock_date(api, world):
    """429, not 400: the request is well-formed and would succeed later."""
    alice, _, _ = world
    _login(api, alice)
    for n in range(INVITE_BUDGET):
        spent = await api.post(
            "/api/invites", json={"email": f"n{n}@x.com"}, headers=CSRF
        )
        assert spent.status_code == 200

    refused = await api.post(
        "/api/invites", json={"email": "one-too-many@x.com"}, headers=CSRF
    )
    assert refused.status_code == 429
    assert refused.json()["error"] == "invite_budget"
    assert "unlocks" in refused.json()["detail"]


async def test_cove_invite_shares_the_budget_over_http(api, world):
    """The bypass, closed at the HTTP layer too.

    Spending the budget through the reef door must leave the cove door
    refusing as well, or a junk cove reopens the hole.
    """
    alice, _, _ = world
    _login(api, alice)
    for n in range(INVITE_BUDGET):
        await api.post("/api/invites", json={"email": f"r{n}@x.com"}, headers=CSRF)

    refused = await api.post(
        "/api/coves/team/invites", json={"email": "via-cove@x.com"}, headers=CSRF
    )
    assert refused.status_code == 429


async def test_leaving_over_http_hands_the_cove_on(api, world):
    """The owner departs; the cove survives, owned by whoever is left."""
    alice, bob, _ = world
    _login(api, alice)
    left = await api.post("/api/coves/team/leave", headers=CSRF)
    assert left.status_code == 200
    assert left.json()["handed_to"] == "Bob"

    # Alice is out — 404 rather than 403, so leaving cannot be used to probe
    # which coves still exist.
    assert (await api.get("/api/coves/team/members")).status_code == 404
    # ...and Bob still has it.
    api.cookies.clear()
    _login(api, bob)
    assert (await api.get("/api/coves/team/members")).status_code == 200


async def test_deleting_requires_the_cove_name_as_confirmation(api, world):
    """A DELETE without the typed name is refused before anything is touched."""
    alice, _, _ = world
    _login(api, alice)
    for body in ({}, {"confirmation": "DELETE"}, {"confirmation": "other-cove"}):
        response = await api.request(
            "DELETE", "/api/coves/team", json=body, headers=CSRF
        )
        assert response.status_code == 400
        assert response.json()["error"] == "bad_request"
    assert (await api.get("/api/coves/team/members")).status_code == 200


async def test_deleting_a_shared_cove_over_http_is_refused(api, world):
    """Confirmation is not authority: somebody else is still in there."""
    alice, _, _ = world
    _login(api, alice)
    response = await api.request(
        "DELETE", "/api/coves/team", json={"confirmation": "team"}, headers=CSRF
    )
    assert response.status_code == 400
    assert (await api.get("/api/coves/team/members")).status_code == 200


async def test_deleting_the_last_cove_over_http_destroys_it(api, world):
    alice, _, _ = world
    _login(api, alice)
    await api.delete("/api/coves/team/members/bob@x.com", headers=CSRF)
    response = await api.request(
        "DELETE", "/api/coves/team", json={"confirmation": "team"}, headers=CSRF
    )
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    index = await api.get("/api/index")
    assert "team" not in {cove["alias"] for cove in index.json()["coves"]}


async def test_delete_removes_a_page_over_http(api, world):
    """The web surface can take a mistyped page back out again."""
    alice, _bob, _ = world
    _login(api, alice)
    await api.put(
        "/api/pages/team/notes/typo.md",
        json={"body": "oops", "message": "create"},
        headers=CSRF,
    )
    removed = await api.delete("/api/pages/team/notes/typo.md", headers=CSRF)
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True
    assert (await api.get("/api/pages/team/notes/typo.md")).status_code == 404


async def test_delete_of_a_missing_page_is_404(api, world):
    """A path naming nothing is a clean 404, not a 500."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.delete("/api/pages/team/notes/nope.md", headers=CSRF)
    assert response.status_code == 404


async def test_delete_requires_the_csrf_header(api, world):
    """Destroying a page sits behind the same guard as writing one."""
    alice, _bob, _ = world
    _login(api, alice)
    assert (await api.delete("/api/pages/team/notes/a.md")).status_code == 403


async def test_put_normalizes_a_new_pages_path(api, world):
    """A path typed without ``.md`` lands under the tidy name."""
    alice, _bob, _ = world
    _login(api, alice)
    created = await api.put(
        "/api/pages/team/Notes/Packing List",
        json={"body": "socks", "message": "create"},
        headers=CSRF,
    )
    assert created.status_code == 200
    assert created.json()["path"] == "notes/packing-list.md"


async def test_put_with_an_unrepairable_path_explains_itself(api, world):
    """The 400 carries the reason, which the editor shows verbatim.

    Percent-encoded so the ``?`` arrives as part of the path rather than
    starting a query string -- which is exactly how a browser would send it,
    and proves the check runs on the decoded value.
    """
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/team/notes/what%3F.md",
        json={"body": "x", "message": "m"},
        headers=CSRF,
    )
    assert response.status_code == 400
    assert "'?'" in response.json()["detail"]


async def test_renaming_a_cove_over_http_moves_only_my_own_name(api, world, graph):
    """The alias is a column on the caller's membership, so a rename is
    invisible to everybody else -- and it is how somebody admitted under a
    suffixed name repairs it."""
    alice, bob, _team = world
    _login(api, alice)

    renamed = await api.post(
        "/api/coves/team/name", json={"name": "squad"}, headers={"x-reef-csrf": "1"}
    )
    assert renamed.status_code == 200
    assert renamed.json() == {"was": "team", "now": "squad"}

    aliases = {s["alias"] for s in (await api.get("/api/index")).json()["coves"]}
    assert "squad" in aliases and "team" not in aliases

    # Bob still calls it what he always called it.
    _login(api, bob)
    his = {s["alias"] for s in (await api.get("/api/index")).json()["coves"]}
    assert "team" in his and "squad" not in his


async def test_renaming_to_a_name_i_already_use_is_refused(api, world, graph):
    alice, _bob, _team = world
    await graph.shared_cove("boat", alice)
    _login(api, alice)

    clash = await api.post(
        "/api/coves/team/name", json={"name": "boat"}, headers={"x-reef-csrf": "1"}
    )
    assert clash.status_code == 400
    assert "already have a cove" in clash.json()["detail"]
