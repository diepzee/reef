"""Read API: membership slicing, page fetch, 401s."""

import httpx
import pytest_asyncio

from rif.db import transaction_scope
from rif.pages import save_page
from rif.server import mcp
from rif.web.routes_api import register_api_routes


@pytest_asyncio.fixture
async def api(monkeypatch, graph):
    """Stand up an HTTP client against the read API with a fixed session secret.

    :param monkeypatch: pytest's monkeypatch fixture
    :param graph: the topology-builder fixture, pulled in for fixture ordering
    :returns: an async client bound to the FastMCP ASGI app
    """
    from rif.config import get_settings

    monkeypatch.setattr(get_settings(), "session_secret", "test-secret")
    register_api_routes(mcp)
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="https://rif.example"
    ) as client:
        yield client


def _login(client: httpx.AsyncClient, person) -> None:
    """Seal a session token for ``person`` and attach it to the client's cookies.

    :param client: the HTTP client to log in
    :param person: the person to seal a session for
    """
    from rif.web.session import seal

    token = seal(person.id, person.email, secret="test-secret")
    client.cookies.set("rif_session", token)


@pytest_asyncio.fixture
async def world(graph):
    """Two people; alice owns 'team' with bob; carol is elsewhere.

    Builders run outside a transaction so the seeded rows are committed
    before any HTTP request runs -- the handlers under test open their own
    ``transaction_scope()`` and would not see uncommitted work.

    :param graph: the topology-builder fixture
    :returns: a tuple of ``(alice, bob, team)``
    """
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    await graph.personal_space(alice)
    await graph.personal_space(bob)
    team = await graph.shared_space("team", alice, bob)
    return alice, bob, team


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
