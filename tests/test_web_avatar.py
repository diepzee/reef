"""Avatar endpoints: set, serve, clear, and the limits on what may be stored."""

import base64

from conftest import _login

from reef.web.routes_api import AVATAR_MAX_BYTES

CSRF = {"X-Rif-Csrf": "1"}

#: The smallest thing that is really a PNG, so the tests exercise bytes
#: rather than a placeholder string.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


async def test_me_reports_no_avatar_until_one_is_set(api, world):
    """A person who has chosen no picture reports ``avatar`` as null."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.get("/api/me")
    assert response.status_code == 200
    assert response.json()["avatar"] is None


async def test_put_then_get_round_trips_the_bytes(api, world):
    """A stored avatar comes back byte-for-byte with its declared type."""
    alice, _bob, _ = world
    _login(api, alice)
    stored = await api.put(
        "/api/me/avatar",
        json={"mime": "image/png", "data": base64.b64encode(PNG).decode()},
        headers=CSRF,
    )
    assert stored.status_code == 200
    assert stored.json()["avatar"] == f"/api/me/avatar?v={len(PNG)}"

    assert (await api.get("/api/me")).json()["avatar"] is not None

    served = await api.get("/api/me/avatar")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content == PNG


async def test_get_without_an_avatar_is_404(api, world):
    """Serving an unset avatar is a clean 404, not an empty 200."""
    alice, _bob, _ = world
    _login(api, alice)
    assert (await api.get("/api/me/avatar")).status_code == 404


async def test_delete_clears_it(api, world):
    """Removing an avatar returns the person to their initials."""
    alice, _bob, _ = world
    _login(api, alice)
    await api.put(
        "/api/me/avatar",
        json={"mime": "image/png", "data": base64.b64encode(PNG).decode()},
        headers=CSRF,
    )
    cleared = await api.delete("/api/me/avatar", headers=CSRF)
    assert cleared.status_code == 200
    assert cleared.json()["avatar"] is None
    assert (await api.get("/api/me")).json()["avatar"] is None
    assert (await api.get("/api/me/avatar")).status_code == 404


async def test_svg_is_refused(api, world):
    """SVG is a script carrier and this endpoint serves bytes to a browser."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/me/avatar",
        json={
            "mime": "image/svg+xml",
            "data": base64.b64encode(b"<svg/>").decode(),
        },
        headers=CSRF,
    )
    assert response.status_code == 400


async def test_oversized_picture_is_refused(api, world):
    """A picture past the ceiling is refused rather than silently truncated."""
    alice, _bob, _ = world
    _login(api, alice)
    too_big = base64.b64encode(b"\x00" * (AVATAR_MAX_BYTES + 1)).decode()
    response = await api.put(
        "/api/me/avatar",
        json={"mime": "image/png", "data": too_big},
        headers=CSRF,
    )
    assert response.status_code == 400
    assert (await api.get("/api/me/avatar")).status_code == 404


async def test_a_refusal_names_its_reason(api, world):
    """A refused picture says why, rather than a bare ``bad_request``.

    The handler writes a reason for every refusal -- the ceiling in kB, the
    types it accepts -- and a 400 that drops it leaves the person with
    nothing to act on and reads as a bug in the app rather than a rule.
    """
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/me/avatar",
        json={
            "mime": "image/png",
            "data": base64.b64encode(b"\x00" * (AVATAR_MAX_BYTES + 1)).decode(),
        },
        headers=CSRF,
    )
    assert response.status_code == 400
    assert "512kB" in response.json()["detail"]


async def test_undecodable_data_is_refused(api, world):
    """Data that is not base64 is a bad request, not a 500."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/me/avatar",
        json={"mime": "image/png", "data": "not base64 at all!"},
        headers=CSRF,
    )
    assert response.status_code == 400


async def test_write_requires_the_csrf_header(api, world):
    """Both mutations sit behind the same CSRF header as every other write."""
    alice, _bob, _ = world
    _login(api, alice)
    unguarded = await api.put(
        "/api/me/avatar",
        json={"mime": "image/png", "data": base64.b64encode(PNG).decode()},
    )
    assert unguarded.status_code == 403
    assert (await api.delete("/api/me/avatar")).status_code == 403


async def test_an_avatar_is_private_to_its_owner(api, world):
    """One person's picture is never served on another person's session.

    ``/api/me/avatar`` is scoped to the caller by construction -- there is no
    id in the path to tamper with -- and this pins that: Bob asking for "my
    avatar" gets his own absence, not Alice's picture.
    """
    alice, bob, _ = world
    _login(api, alice)
    await api.put(
        "/api/me/avatar",
        json={"mime": "image/png", "data": base64.b64encode(PNG).decode()},
        headers=CSRF,
    )
    _login(api, bob)
    assert (await api.get("/api/me/avatar")).status_code == 404
    assert (await api.get("/api/me")).json()["avatar"] is None


async def _give_alice_a_face(api, alice) -> None:
    """Store a picture for ``alice`` through the API, as she would herself.

    :param api: the HTTP client
    :param alice: the person to give a picture to
    """
    _login(api, alice)
    stored = await api.put(
        "/api/me/avatar",
        json={"mime": "image/png", "data": base64.b64encode(PNG).decode()},
        headers=CSRF,
    )
    assert stored.status_code == 200


async def test_the_roster_points_a_co_member_at_the_picture(api, world):
    """Bob's roster carries a URL for Alice's face, and null for his own absence."""
    alice, bob, _ = world
    await _give_alice_a_face(api, alice)

    api.cookies.clear()
    _login(api, bob)
    roster = (await api.get("/api/spaces/team/members")).json()["members"]
    by_name = {member["display_name"]: member for member in roster}

    assert by_name["Alice"]["avatar"] == (
        f"/api/spaces/team/members/{by_name['Alice']['person_id']}/avatar?v={len(PNG)}"
    )
    assert by_name["Bob"]["avatar"] is None


async def test_a_co_member_is_served_the_picture(api, world):
    """The bug this fixes: everybody but you rendered as a coloured initial."""
    alice, bob, _ = world
    await _give_alice_a_face(api, alice)

    api.cookies.clear()
    _login(api, bob)
    roster = (await api.get("/api/spaces/team/members")).json()["members"]
    url = next(m["avatar"] for m in roster if m["display_name"] == "Alice")

    served = await api.get(url)
    assert served.status_code == 200
    assert served.content == PNG
    assert served.headers["content-type"].startswith("image/png")
    # Never a shared cache: this body is only for people in the cove.
    assert "private" in served.headers["cache-control"]
    # One person's bytes reaching another person's browser is the one place
    # a sniffed content type could turn a picture into something active.
    assert served.headers["x-content-type-options"] == "nosniff"


async def test_a_stranger_cannot_fetch_a_members_picture(api, world, graph):
    """Someone outside the cove gets the same 404 as a cove that is not there."""
    alice, _bob, _ = world
    await _give_alice_a_face(api, alice)
    roster = (await api.get("/api/spaces/team/members")).json()["members"]
    url = next(m["avatar"] for m in roster if m["display_name"] == "Alice")

    stranger = await graph.person("stranger@example.test", "Stranger")
    await graph.personal_space(stranger)
    api.cookies.clear()
    _login(api, stranger)

    assert (await api.get(url)).status_code == 404


async def test_a_malformed_person_id_is_a_404_not_a_500(api, world):
    """The id is in the path, so it arrives as whatever the caller typed."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.get("/api/spaces/team/members/not-a-uuid/avatar")
    assert response.status_code == 404
