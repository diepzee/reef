"""The reef page through FastMCP's real consent handlers, GET to POST."""

import re
import time

import httpx
import pytest_asyncio
from fastmcp import FastMCP
from fastmcp.server.auth.oauth_proxy.models import OAuthTransaction

from reef.config import get_settings
from reef.server import _build_auth

DOMAIN = "https://example-authkit.test"
BASE = "https://reef.example.test"
KEY = "ab" * 32
CLIENT_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest_asyncio.fixture
async def consent_client(monkeypatch, tmp_path):
    """A client against a proxy-configured server with one seeded txn.

    Reuses the same env recipe as tests/test_auth_provider.py; the
    transaction is seeded directly into the provider's store, which is
    what /authorize would have done before redirecting here.
    """
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", DOMAIN)
    monkeypatch.setenv("RIF_BASE_URL", BASE)
    monkeypatch.setenv("WORKOS_MCP_CLIENT_ID", "client_mcp_123")
    monkeypatch.setenv("WORKOS_MCP_CLIENT_SECRET", "sk_test_456")
    monkeypatch.setattr(get_settings(), "jwt_signing_key", KEY)
    monkeypatch.setattr(get_settings(), "oauth_store_dir", str(tmp_path / "o"))
    monkeypatch.setattr(get_settings(), "allowed_client_redirects", "")

    provider = _build_auth()
    txn = OAuthTransaction(
        txn_id="txn-1",
        client_id="client-abc",
        client_redirect_uri=CLIENT_REDIRECT,
        client_state="state-xyz",
        code_challenge=None,
        code_challenge_method="S256",
        scopes=["openid"],
        created_at=time.time(),
    )
    await provider._transaction_store.put(key="txn-1", value=txn, ttl=900)

    server = FastMCP("probe", auth=provider)
    transport = httpx.ASGITransport(app=server.http_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="https://test"
    ) as client:
        yield client


async def _get_consent(client: httpx.AsyncClient) -> tuple[str, str]:
    """GET the consent page; return (html, csrf_token embedded in it)."""
    response = await client.get("/consent?txn_id=txn-1")
    assert response.status_code == 200
    html = response.text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no CSRF token in the rendered page"
    return html, match.group(1)


async def test_get_renders_the_reef_page(consent_client):
    """Through the real handler -- not the renderer called directly."""
    html, _ = await _get_consent(consent_client)
    assert "wants to access your reef" in html
    assert "Read everything you can see" in html
    assert "claude.ai" in html


async def test_approve_continues_to_authkit(consent_client):
    """POSTing the form our page emits moves the flow upstream.

    Same httpx client, so the CSRF cookie from the GET rides along --
    exactly what a browser does.
    """
    _, csrf = await _get_consent(consent_client)
    response = await consent_client.post(
        "/consent",
        data={"txn_id": "txn-1", "csrf_token": csrf, "action": "approve"},
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(DOMAIN)
    assert "/oauth2/authorize" in location
    assert "offline_access" in location


async def test_deny_returns_to_the_client_with_access_denied(consent_client):
    """Deny goes back to the client's redirect with the standard error."""
    _, csrf = await _get_consent(consent_client)
    response = await consent_client.post(
        "/consent",
        data={"txn_id": "txn-1", "csrf_token": csrf, "action": "deny"},
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(CLIENT_REDIRECT)
    assert "error=access_denied" in location
    assert "state=state-xyz" in location


async def test_wrong_csrf_is_rejected(consent_client):
    """A forged POST without the issued token must not advance the flow."""
    await _get_consent(consent_client)
    response = await consent_client.post(
        "/consent",
        data={"txn_id": "txn-1", "csrf_token": "forged", "action": "approve"},
    )
    assert response.status_code == 400
