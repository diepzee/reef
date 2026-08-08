"""Auth route flow with a fake OIDC upstream."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest_asyncio

from rif.server import mcp
from rif.web.oidc import OIDCError
from rif.web.routes_auth import register_auth_routes


class RaisingOIDC:
    """A fake OIDC client whose token exchange always fails upstream."""

    async def exchange(self, code, verifier, redirect_uri):
        """Raise, simulating a failed call to the token endpoint.

        :param code: the authorization code, unused
        :param verifier: the PKCE verifier, unused
        :param redirect_uri: the callback redirect URI, unused
        :raises OIDCError: always
        """
        raise OIDCError("token endpoint unreachable")

    async def userinfo(self, access_token):
        """Never reached; exchange always raises first.

        :param access_token: unused
        :raises OIDCError: always
        """
        raise OIDCError("unreachable")


class FakeOIDC:
    """A stand-in OIDC client that never calls the network."""

    def __init__(self, claims: dict):
        """Store the claims userinfo should return.

        :param claims: the userinfo claims to hand back
        """
        self.claims = claims
        self.exchanged: list[tuple[str, str, str]] = []

    async def exchange(self, code, verifier, redirect_uri):
        """Record the exchange call and return a fixed fake token.

        :param code: the authorization code
        :param verifier: the PKCE verifier
        :param redirect_uri: the callback redirect URI
        :returns: a fixed fake access token
        """
        self.exchanged.append((code, verifier, redirect_uri))
        return "fake-access-token"

    async def userinfo(self, access_token):
        """Return the stored claims for the expected fake token.

        :param access_token: the bearer token from :meth:`exchange`
        :returns: the stored claims
        """
        assert access_token == "fake-access-token"
        return self.claims


@pytest_asyncio.fixture
async def web(monkeypatch, graph):
    """Wire a fake OIDC client into the auth routes against a seeded member.

    :returns: a tuple of the HTTP client, the fake OIDC client, and the
        already-invited member person
    """
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", "fake.authkit.app")
    monkeypatch.setenv("WORKOS_CLIENT_ID", "client_123")
    monkeypatch.setenv("RIF_BASE_URL", "https://rif.example")
    from rif.config import get_settings

    monkeypatch.setattr(get_settings(), "session_secret", "test-secret")
    person = await graph.person("member@example.com", "Member")
    fake = FakeOIDC(
        {"sub": "sub_1", "email": "member@example.com", "email_verified": True}
    )
    register_auth_routes(mcp, client_factory=lambda: fake)
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="https://rif.example"
    ) as client:
        yield client, fake, person


async def test_login_redirects_to_authkit(web):
    """The login route redirects to AuthKit with PKCE and CSRF-state params."""
    client, _fake, _ = web
    response = await client.get("/api/auth/login")
    assert response.status_code == 302
    url = urlparse(response.headers["location"])
    assert url.hostname == "fake.authkit.app"
    query = parse_qs(url.query)
    assert query["client_id"] == ["client_123"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://rif.example/api/auth/callback"]
    assert "rif_oauth" in response.cookies


async def test_callback_binds_and_sets_session(web):
    """A matching callback exchanges the code and sets the session cookie."""
    client, fake, _person = web
    login = await client.get("/api/auth/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    response = await client.get(
        "/api/auth/callback", params={"code": "code_abc", "state": state}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/app"
    assert "rif_session" in response.cookies
    assert fake.exchanged[0][0] == "code_abc"


async def test_callback_rejects_bad_state(web):
    """A state mismatch is rejected before any exchange happens."""
    client, _, _ = web
    await client.get("/api/auth/login")
    response = await client.get(
        "/api/auth/callback", params={"code": "c", "state": "wrong"}
    )
    assert response.status_code == 400


async def test_callback_unknown_email_gets_403(web):
    """An email nobody invited is denied with a plain-language 403 page."""
    client, fake, _ = web
    fake.claims = {"sub": "sub_2", "email": "stranger@x.com", "email_verified": True}
    login = await client.get("/api/auth/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    response = await client.get(
        "/api/auth/callback", params={"code": "c", "state": state}
    )
    assert response.status_code == 403


async def test_login_unconfigured_is_503(monkeypatch):
    """Login 503s cleanly when AuthKit is not configured.

    Unset all three env vars ``login`` checks -- domain, client id, and base
    URL -- rather than relying on whatever the ambient test environment
    happens to leave unset.
    """
    monkeypatch.delenv("WORKOS_AUTHKIT_DOMAIN", raising=False)
    monkeypatch.delenv("WORKOS_CLIENT_ID", raising=False)
    monkeypatch.delenv("RIF_BASE_URL", raising=False)
    register_auth_routes(mcp, client_factory=lambda: FakeOIDC({}))
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="https://rif.example"
    ) as client:
        response = await client.get("/api/auth/login")
    assert response.status_code == 503
    assert response.json() == {"error": "auth_unconfigured"}


async def test_callback_oidc_upstream_error_is_502(web):
    """An OIDC exchange failure maps to a 502, not a raw exception."""
    client, _fake, _ = web
    login = await client.get("/api/auth/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    register_auth_routes(mcp, client_factory=lambda: RaisingOIDC())
    response = await client.get(
        "/api/auth/callback", params={"code": "c", "state": state}
    )
    assert response.status_code == 502
    assert response.json() == {"error": "oidc_upstream"}


async def test_logout_requires_csrf(web):
    """A logout POST without the CSRF header is rejected with 403."""
    client, _fake, _ = web
    response = await client.post("/api/auth/logout")
    assert response.status_code == 403
    assert response.json() == {"error": "csrf"}


async def test_logout_clears_session(web):
    """A logout POST with the CSRF header succeeds and deletes the cookie."""
    client, _fake, _ = web
    client.cookies.set("rif_session", "some-token")
    response = await client.post(
        "/api/auth/logout", headers={"x-rif-csrf": "1"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any(
        header.startswith("rif_session=") and "Max-Age=0" in header
        for header in set_cookie_headers
    )
