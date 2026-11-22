"""The test-account routes: reset and readiness, for the signed-in test account."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from importlib.metadata import version

import asyncpg
import httpx
import pytest
import pytest_asyncio
from conftest import Graph
from fastmcp import Client

from reef.access import Principal, arm
from reef.db import transaction_scope
from reef.engrams import save_engram
from reef.models import Engram, Person
from reef.testaccount.fixture import read_fixture

GOOD = "good-token"


@dataclass
class _Token:
    """A verified token, as the provider hands it back."""

    claims: dict[str, str]


class _StubAuth:
    """An auth provider that accepts one token and names its subject."""

    def __init__(self, subject: str) -> None:
        """Remember who the good token belongs to.

        :param subject: the subject the good token carries
        """
        self.subject = subject

    async def verify_token(self, token: str) -> _Token | None:
        """Accept only the good token.

        :param token: the bearer token
        :returns: the verified token, or None
        """
        if token != GOOD:
            return None
        return _Token(claims={"sub": self.subject})


@pytest_asyncio.fixture
async def route(
    monkeypatch: pytest.MonkeyPatch, graph: Graph
) -> AsyncIterator[httpx.AsyncClient]:
    """Serve the test-account routes with a stub auth provider.

    :param monkeypatch: pytest's monkeypatch fixture
    :param graph: the topology builders
    :returns: an async client against the server's app
    """
    from reef.server import mcp
    from reef.web.routes_test_account import register_test_account_routes

    register_test_account_routes(mcp)
    app = mcp.http_app()
    # Set after the app is built: the app would otherwise ask the stub for
    # middleware. The routes read the provider from the server per request.
    monkeypatch.setattr(mcp, "auth", _StubAuth("sub-1"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://reef.example"
    ) as client:
        yield client


def _bearer(token: str = GOOD) -> dict[str, str]:
    """Build an Authorization header.

    :param token: the bearer token
    :returns: the headers
    """
    return {"authorization": f"Bearer {token}"}


async def _person(graph: Graph, seed: asyncpg.Connection, flagged: bool) -> Person:
    """Create a person with a personal space and a subject, maybe flagged.

    :param graph: the topology builders
    :param seed: the connection that is not bound by row security
    :param flagged: whether to flag them as a test account
    :returns: the person
    """
    person = await graph.person("solo@example.test", "Solo", subject="sub-1")
    await graph.personal_space(person)
    if flagged:
        await seed.execute(
            "INSERT INTO test_accounts (person_id) VALUES ($1)", person.id
        )
    return person


async def test_reset_without_token_is_401(route):
    """A reset without a bearer token is refused as unauthenticated."""
    response = await route.post("/api/test-account/reset")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthenticated"}


async def test_reset_with_bad_token_is_401(route):
    """A reset with a token the provider rejects is refused as unauthenticated."""
    response = await route.post("/api/test-account/reset", headers=_bearer("nope"))
    assert response.status_code == 401
    assert response.json() == {"error": "unauthenticated"}


async def test_ready_without_token_is_401(route):
    """Readiness without a bearer token is refused."""
    response = await route.get("/api/test-account/ready")
    assert response.status_code == 401


async def test_reset_for_unflagged_is_403(route, graph, seed):
    """A person who is not flagged cannot reset, and keeps their engrams."""
    person = await _person(graph, seed, flagged=False)
    principal = Principal(person_id=person.id, email=person.email)
    async with transaction_scope():
        await arm(principal)
        await save_engram(principal, "personal", "keep.md", "mine", message="seed")
    response = await route.post("/api/test-account/reset", headers=_bearer())
    assert response.status_code == 403
    assert response.json() == {"error": "not_a_test_account"}
    async with transaction_scope():
        await arm(principal)
        assert await Engram.count() > 0


async def test_ready_for_unflagged_is_403(route, graph, seed):
    """A person who is not flagged cannot ask for readiness."""
    await _person(graph, seed, flagged=False)
    response = await route.get("/api/test-account/ready", headers=_bearer())
    assert response.status_code == 403
    assert response.json() == {"error": "not_a_test_account"}


async def test_reset_for_flagged_is_200(route, graph, seed):
    """A flagged person's reset gives the fixture and Reef versions and what it loaded."""
    await _person(graph, seed, flagged=True)
    response = await route.post("/api/test-account/reset", headers=_bearer())
    assert response.status_code == 200
    body = response.json()
    assert body["fixture_version"] == read_fixture().version
    assert body["reef_version"] == version("reef")
    assert {"engrams", "files", "services"} <= body.keys()


async def test_reset_shared_with_others_is_403(route, graph, seed):
    """A flagged person who shares a space with someone else cannot reset."""
    person = await _person(graph, seed, flagged=True)
    other = await graph.person("other@example.test", "Other")
    await graph.shared_space("team", person, other)
    response = await route.post("/api/test-account/reset", headers=_bearer())
    assert response.status_code == 403
    assert response.json() == {"error": "shared_with_others"}


async def test_ready_reports_counts(route, graph, seed):
    """Readiness says whether search is ready, and what it still waits for."""
    await _person(graph, seed, flagged=True)
    response = await route.get("/api/test-account/ready", headers=_bearer())
    assert response.status_code == 200
    assert response.json().keys() == {
        "ready",
        "vectors_missing",
        "files_unread",
        "jobs_waiting",
    }


async def test_reset_is_not_an_mcp_tool():
    """The reset is a route only: no MCP tool resets."""
    from reef.server import mcp

    async with Client(mcp) as client:
        names = [tool.name for tool in await client.list_tools()]
    assert names
    assert not [name for name in names if "reset" in name]
