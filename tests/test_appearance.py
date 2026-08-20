"""Per-person cove appearance: chosen looks are private and per viewer."""

from conftest import _login

CSRF = {"X-Rif-Csrf": "1"}


async def test_nothing_chosen_is_an_empty_map(api, world):
    """A person who has picked nothing reports no choices, not nulls."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.get("/api/appearance")
    assert response.status_code == 200
    assert response.json() == {"coves": {}}


async def test_a_choice_round_trips(api, world):
    """A stored look comes back keyed by the cove's slug."""
    alice, _bob, _ = world
    _login(api, alice)
    stored = await api.put(
        "/api/coves/team/appearance",
        json={"color": "violet", "glyph": "spiral"},
        headers=CSRF,
    )
    assert stored.status_code == 200
    assert stored.json() == {"color": "violet", "glyph": "spiral"}
    assert (await api.get("/api/appearance")).json() == {
        "coves": {"team": {"color": "violet", "glyph": "spiral"}}
    }


async def test_choosing_again_replaces_rather_than_stacks(api, world):
    """The second choice for a cove overwrites the first."""
    alice, _bob, _ = world
    _login(api, alice)
    for color in ("violet", "amber", "sky"):
        await api.put(
            "/api/coves/team/appearance",
            json={"color": color, "glyph": None},
            headers=CSRF,
        )
    assert (await api.get("/api/appearance")).json() == {
        "coves": {"team": {"color": "sky", "glyph": None}}
    }


async def test_clearing_both_removes_the_choice_entirely(api, world):
    """Back to derived is the absence of a row, not a row of nulls."""
    alice, _bob, _ = world
    _login(api, alice)
    await api.put(
        "/api/coves/team/appearance",
        json={"color": "pink", "glyph": "tubes"},
        headers=CSRF,
    )
    cleared = await api.put(
        "/api/coves/team/appearance",
        json={"color": None, "glyph": None},
        headers=CSRF,
    )
    assert cleared.status_code == 200
    assert (await api.get("/api/appearance")).json() == {"coves": {}}


async def test_one_persons_look_is_invisible_to_another(api, world):
    """The whole point: two members see the same cove differently.

    Bob shares ``team`` with Alice. Alice restyling it must not change what
    Bob is served, and must not even be visible to him.
    """
    alice, bob, _ = world
    _login(api, alice)
    await api.put(
        "/api/coves/team/appearance",
        json={"color": "lime", "glyph": "bubbles"},
        headers=CSRF,
    )
    _login(api, bob)
    assert (await api.get("/api/appearance")).json() == {"coves": {}}

    # And Bob's own choice for the same cove leaves Alice's untouched.
    await api.put(
        "/api/coves/team/appearance",
        json={"color": "orange", "glyph": None},
        headers=CSRF,
    )
    assert (await api.get("/api/appearance")).json() == {
        "coves": {"team": {"color": "orange", "glyph": None}}
    }
    _login(api, alice)
    assert (await api.get("/api/appearance")).json() == {
        "coves": {"team": {"color": "lime", "glyph": "bubbles"}}
    }


async def test_an_unoffered_colour_is_refused(api, world):
    """Only names from the palette are stored -- never a raw hex value."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/coves/team/appearance",
        json={"color": "#ff00ff", "glyph": None},
        headers=CSRF,
    )
    assert response.status_code == 400
    assert (await api.get("/api/appearance")).json() == {"coves": {}}


async def test_a_retired_body_plan_is_refused(api, world):
    """``brain`` is still grown by the hash but is not on offer as a choice."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/coves/team/appearance",
        json={"color": None, "glyph": "brain"},
        headers=CSRF,
    )
    assert response.status_code == 400


async def test_a_cove_you_cannot_see_cannot_be_styled(api, world):
    """Appearance resolves the cove first, so it is no way to probe for one."""
    _alice, bob, _ = world
    _login(api, bob)
    response = await api.put(
        "/api/coves/nowhere/appearance",
        json={"color": "amber", "glyph": None},
        headers=CSRF,
    )
    assert response.status_code in (403, 404)


async def test_the_personal_cove_can_be_styled_too(api, world):
    """Personal is pinned seafoam by derivation, not by decree."""
    alice, _bob, _ = world
    _login(api, alice)
    stored = await api.put(
        "/api/coves/personal/appearance",
        json={"color": "indigo", "glyph": None},
        headers=CSRF,
    )
    assert stored.status_code == 200
    assert (await api.get("/api/appearance")).json()["coves"]["personal"] == {
        "color": "indigo",
        "glyph": None,
    }


async def test_writing_a_look_requires_the_csrf_header(api, world):
    """Same guard as every other mutation on this surface."""
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/coves/team/appearance", json={"color": "sky", "glyph": None}
    )
    assert response.status_code == 403
