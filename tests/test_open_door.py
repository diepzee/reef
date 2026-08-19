"""The launch exception: a door that admits strangers, and closes itself."""

import asyncio
from datetime import date
from urllib.parse import parse_qs, urlparse

import asyncpg
import httpx
import pytest
import pytest_asyncio
from conftest import seed_dsn
from test_web_auth_routes import FakeOIDC

from reef.opendoor import admit, door_policy
from reef.server import mcp
from reef.web.routes_auth import register_auth_routes


def _env(monkeypatch, seats: str | None, until: str | None) -> None:
    """Set or clear the two settings the door reads.

    :param monkeypatch: pytest's environment patcher
    :param seats: value for ``RIF_OPEN_SEATS``, or None to leave it unset
    :param until: value for ``RIF_OPEN_UNTIL``, or None to leave it unset
    """
    from reef.config import get_settings

    for name, value in (("RIF_OPEN_SEATS", seats), ("RIF_OPEN_UNTIL", until)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Drop the settings singleton around each test, so env changes take.

    :yields: nothing; the cache is cleared before and after
    """
    from reef.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_door_is_closed_when_nothing_is_configured(monkeypatch):
    """An unconfigured deployment -- production today -- admits nobody."""
    _env(monkeypatch, None, None)
    assert door_policy().is_open is False


def test_door_is_closed_when_only_seats_are_set(monkeypatch):
    """Seats without a date is a door with no closing time, so it stays shut."""
    _env(monkeypatch, "500", None)
    policy = door_policy()
    assert policy.is_open is False
    assert "RIF_OPEN_UNTIL" in policy.reason


def test_door_is_closed_when_only_a_date_is_set(monkeypatch):
    """A date without seats is an unbounded door, so it stays shut."""
    _env(monkeypatch, None, "2026-08-27")
    policy = door_policy()
    assert policy.is_open is False
    assert "RIF_OPEN_SEATS" in policy.reason


def test_door_is_open_when_both_are_set_and_the_date_is_ahead(monkeypatch):
    """The launch configuration: seats to spend and a day to stop on."""
    _env(monkeypatch, "500", "2026-08-27")
    policy = door_policy(today=date(2026, 8, 20))
    assert policy.is_open is True
    assert policy.seats == 500


def test_door_is_open_on_the_closing_day_itself(monkeypatch):
    """``until`` is inclusive: the door closes when that day ends, not starts."""
    _env(monkeypatch, "500", "2026-08-27")
    assert door_policy(today=date(2026, 8, 27)).is_open is True


def test_door_closes_itself_the_day_after(monkeypatch):
    """The whole point of the date: nobody has to remember to flip it back."""
    _env(monkeypatch, "500", "2026-08-27")
    policy = door_policy(today=date(2026, 8, 28))
    assert policy.is_open is False
    assert "2026-08-27" in policy.reason


def test_zero_seats_closes_the_door(monkeypatch):
    """The off switch that does not need a redeploy to remove the date."""
    _env(monkeypatch, "0", "2026-08-27")
    assert door_policy(today=date(2026, 8, 20)).is_open is False


def test_an_unparseable_date_closes_the_door(monkeypatch):
    """A typo in the date must fail closed, and say so rather than admitting."""
    _env(monkeypatch, "500", "27-08-2026")
    policy = door_policy(today=date(2026, 8, 20))
    assert policy.is_open is False
    assert "27-08-2026" in policy.reason


# --- Admission, against the real database --------------------------------
#
# Real PostgreSQL rather than mocks, for the same reason the invite budget
# tests give: the seat ceiling is enforced inside a function, under a lock,
# and a mocked repository would let every assertion here pass while nothing
# was enforced in production.


def _open(monkeypatch, seats: int = 500) -> None:
    """Open the door with ``seats`` and a date comfortably ahead.

    :param monkeypatch: pytest's environment patcher
    :param seats: the ceiling to configure
    """
    _env(monkeypatch, str(seats), "2099-01-01")


async def _row(seed, email: str) -> dict | None:
    """Read a person row through the policy-free connection.

    :param seed: the seeding connection
    :param email: the address to look up
    :returns: the row, or None
    """
    return await seed.fetchrow(
        "SELECT id, email, subject, display_name, joined_open_door, "
        "invited_by_person_id FROM persons WHERE email = $1",
        email,
    )


async def test_an_admitted_stranger_lands_bound_and_flagged(monkeypatch, seed):
    """The launch path: no invitation anywhere, and they still get in."""
    _open(monkeypatch)
    identity = await admit("stranger@example.test", "sub-stranger", "Stranger")
    assert identity is not None
    assert identity.email == "stranger@example.test"

    row = await _row(seed, "stranger@example.test")
    assert row["subject"] == "sub-stranger"
    assert row["joined_open_door"] is True
    # Nobody invited them, and the flag is what stops that being read as
    # "founding person" by anything counting inviterless rows.
    assert row["invited_by_person_id"] is None


async def test_a_closed_door_admits_nobody_and_writes_nothing(monkeypatch, seed):
    """Production today. The refusal must not leave a half-made row behind."""
    _env(monkeypatch, None, None)
    assert await admit("stranger@example.test", "sub-stranger", "Stranger") is None
    assert await _row(seed, "stranger@example.test") is None


async def test_the_seat_ceiling_refuses_the_one_past_it(monkeypatch, seed):
    """The limit that holds while nobody is awake to enforce it."""
    _open(monkeypatch, seats=2)
    assert await admit("first@example.test", "sub-1", "First") is not None
    assert await admit("second@example.test", "sub-2", "Second") is not None

    assert await admit("third@example.test", "sub-3", "Third") is None
    assert await _row(seed, "third@example.test") is None


async def test_a_genuine_invitation_is_bound_without_spending_a_seat(
    monkeypatch, seed, household
):
    """The race between being invited for real and clicking the button.

    Somebody invited between the OIDC callback and the click already has an
    unbound row. Binding it is right; charging them a launch seat is not,
    because the seat ceiling exists to bound *strangers*.
    """
    _open(monkeypatch, seats=1)
    await seed.execute(
        "INSERT INTO persons (id, email, display_name, invited_by_person_id) "
        "VALUES (gen_random_uuid(), 'invited@example.test', 'Invited', $1)",
        household["wouter"].id,
    )

    identity = await admit("invited@example.test", "sub-invited", "Invited")
    assert identity is not None
    row = await _row(seed, "invited@example.test")
    assert row["subject"] == "sub-invited"
    # Not flagged, so it does not count against the ceiling...
    assert row["joined_open_door"] is False
    assert row["invited_by_person_id"] == household["wouter"].id
    # ...which the single remaining seat proves is still there to spend.
    assert await admit("stranger@example.test", "sub-stranger", "Stranger") is not None


async def test_an_already_bound_address_is_refused_rather_than_rebound(
    monkeypatch, seed, household
):
    """A live account must never be handed to whoever signs in with its address.

    ``rif_person_bind`` matches only unbound rows for this reason; the open
    door needs the same rule, or it becomes an account-takeover route for
    anyone who can prove control of an address a member already uses.
    """
    _open(monkeypatch)
    await seed.execute(
        "UPDATE persons SET subject = 'sub-real' WHERE email = $1",
        household["wouter"].email,
    )
    assert await admit(household["wouter"].email, "sub-attacker", "Not Wouter") is None
    row = await _row(seed, household["wouter"].email)
    assert row["subject"] == "sub-real"


async def test_concurrent_admissions_cannot_oversell_the_last_seat(monkeypatch, seed):
    """Why the count and the insert are one statement under a lock.

    Two admissions racing on the final seat both see it free if the ceiling
    is checked in Python and the insert follows -- the same check-then-act
    the invite budget documents. Run on two connections, because an advisory
    transaction lock serialises nothing when both callers share one.
    """
    _open(monkeypatch, seats=1)
    call = ("SELECT * FROM rif_open_door_admit($1, $2, $3, 1)",)

    async def attempt(nth: int) -> bool:
        """Admit on a connection of its own.

        :param nth: distinguishes the two racers' addresses
        :returns: whether this attempt got the seat
        """
        connection = await asyncpg.connect(seed_dsn())
        try:
            async with connection.transaction():
                rows = await connection.fetch(
                    call[0], f"racer{nth}@example.test", f"sub-{nth}", f"Racer {nth}"
                )
            return bool(rows)
        finally:
            await connection.close()

    got = await asyncio.gather(attempt(1), attempt(2))
    assert sorted(got) == [False, True]

    seated = await seed.fetchval("SELECT count(*) FROM persons WHERE joined_open_door")
    assert seated == 1


# --- The wall, and the button on it --------------------------------------


@pytest_asyncio.fixture
async def door(monkeypatch, graph):
    """Wire the auth routes to a fake OIDC upstream returning a stranger.

    :returns: the HTTP client and the fake OIDC client
    """
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", "fake.authkit.app")
    monkeypatch.setenv("WORKOS_CLIENT_ID", "client_123")
    monkeypatch.setenv("RIF_BASE_URL", "https://reef.example")
    from reef.config import get_settings

    monkeypatch.setattr(get_settings(), "session_secret", "test-secret")
    fake = FakeOIDC(
        {
            "sub": "sub_stranger",
            "email": "stranger@example.test",
            "email_verified": True,
            "name": "A Stranger",
        }
    )
    register_auth_routes(mcp, client_factory=lambda: fake)
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="https://reef.example"
    ) as client:
        yield client, fake


async def _walk_to_the_wall(client) -> httpx.Response:
    """Complete a login and callback for an uninvited address.

    :param client: the HTTP client
    :returns: the callback's response
    """
    login = await client.get("/api/auth/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    return await client.get("/api/auth/callback", params={"code": "c", "state": state})


async def test_a_closed_door_still_shows_the_invite_only_wall(monkeypatch, door):
    """The behaviour every deployment has today must be exactly preserved."""
    client, _ = door
    _env(monkeypatch, None, None)
    response = await _walk_to_the_wall(client)
    assert response.status_code == 403
    assert "stranger@example.test" in response.text
    assert "rif_join" not in response.cookies


async def test_an_open_door_offers_the_button_instead_of_the_wall(monkeypatch, door):
    """The launch path: the dead end becomes a way in."""
    client, _ = door
    _open(monkeypatch)
    response = await _walk_to_the_wall(client)
    assert response.status_code == 200
    assert "/api/auth/join" in response.text
    # The claims have to survive the click, and this cookie is the only
    # place they can: the person has no session yet, by definition.
    assert "rif_join" in response.cookies


async def test_the_button_admits_and_opens_a_session(monkeypatch, door, seed):
    """Pressing it produces an account, a personal space, and a session."""
    client, _ = door
    _open(monkeypatch)
    await _walk_to_the_wall(client)

    response = await client.post("/api/auth/join", headers={"X-Rif-Csrf": "1"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "rif_session" in response.cookies

    row = await _row(seed, "stranger@example.test")
    assert row["joined_open_door"] is True
    spaces = await seed.fetchval(
        "SELECT count(*) FROM spaces WHERE owner_person_id = $1 AND kind = 'personal'",
        row["id"],
    )
    assert spaces == 1


async def test_the_button_needs_the_csrf_header(monkeypatch, door, seed):
    """Same rule as every other mutation; no exception for being logged out."""
    client, _ = door
    _open(monkeypatch)
    await _walk_to_the_wall(client)

    response = await client.post("/api/auth/join")
    assert response.status_code == 403
    assert await _row(seed, "stranger@example.test") is None


async def test_joining_without_the_sealed_claims_is_refused(monkeypatch, door, seed):
    """Posting straight at the route admits nobody: the claims are the proof."""
    client, _ = door
    _open(monkeypatch)
    response = await client.post("/api/auth/join", headers={"X-Rif-Csrf": "1"})
    assert response.status_code == 403
    assert await _row(seed, "stranger@example.test") is None


async def test_tampered_claims_are_refused(monkeypatch, door, seed):
    """The cookie is signed, so an edited address must not become an account."""
    client, _ = door
    _open(monkeypatch)
    await _walk_to_the_wall(client)
    client.cookies.set("rif_join", "eyJlbSI6ICJhZG1pbkBleGFtcGxlLnRlc3QifQ.forged")

    response = await client.post("/api/auth/join", headers={"X-Rif-Csrf": "1"})
    assert response.status_code == 403
    assert await _row(seed, "admin@example.test") is None


async def test_a_door_that_shuts_between_page_and_click_refuses(monkeypatch, door):
    """The seats can run out while somebody is reading the page.

    The button is not authority to enter -- the ceiling is checked again when
    it is pressed, because that is the moment a seat is actually spent.
    """
    client, _ = door
    _open(monkeypatch)
    await _walk_to_the_wall(client)
    _env(monkeypatch, None, None)

    response = await client.post("/api/auth/join", headers={"X-Rif-Csrf": "1"})
    assert response.status_code == 403


async def test_a_member_cannot_forge_the_flag_the_ceiling_counts(probe, household):
    """The seat count must not be writable by the people it bounds.

    ``persons_self_update`` admits the whole of a person's own row, because
    row security cannot say *which column*. Left there, any member could set
    their own ``joined_open_door`` and burn launch seats on rows that never
    came through the door -- not an escalation, but the ceiling is the one
    number this whole design leans on, so it should not be writable by the
    population it limits.

    Through the probe, which neither owns the tables nor bypasses RLS: the
    suite's own role is the owner, and an owner ignores column grants
    entirely, so this same assertion would pass vacuously anywhere else.
    """
    person = household["wouter"]
    await probe.execute("SELECT set_config('app.person_id', $1, false)", str(person.id))
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await probe.execute(
                "UPDATE persons SET joined_open_door = true WHERE id = $1", person.id
            )
        # The columns a person legitimately rewrites on themselves still work,
        # or this narrowing would have broken the profile and logout paths.
        await probe.execute(
            "UPDATE persons SET display_name = 'Renamed' WHERE id = $1", person.id
        )
    finally:
        await probe.execute("SELECT set_config('app.person_id', '', false)")
