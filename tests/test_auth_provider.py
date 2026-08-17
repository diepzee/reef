# tests/test_auth_provider.py
"""Provider selection: proxy when configured, AuthKit fallback, loud refusal."""

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.server.auth.providers.workos import AuthKitProvider, WorkOSProvider
from fastmcp.server.auth.redirect_validation import validate_redirect_uri

from rif.config import get_settings
from rif.server import _DEFAULT_CLIENT_REDIRECTS, _build_auth

DOMAIN = "https://example-authkit.test"
BASE = "https://reef.example.test"
KEY = "ab" * 32


@pytest.fixture
def proxy_env(monkeypatch, tmp_path):
    """Environment + settings for the full proxy branch."""
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", DOMAIN)
    monkeypatch.setenv("RIF_BASE_URL", BASE)
    monkeypatch.setenv("WORKOS_MCP_CLIENT_ID", "client_mcp_123")
    monkeypatch.setenv("WORKOS_MCP_CLIENT_SECRET", "sk_test_456")
    monkeypatch.setattr(get_settings(), "jwt_signing_key", KEY)
    monkeypatch.setattr(get_settings(), "oauth_store_dir", str(tmp_path / "oauth"))
    monkeypatch.setattr(get_settings(), "allowed_client_redirects", "")


def test_unconfigured_returns_none(monkeypatch):
    """No domain/base: stdio dev and the test suite stay auth-free."""
    monkeypatch.delenv("WORKOS_AUTHKIT_DOMAIN", raising=False)
    monkeypatch.delenv("RIF_BASE_URL", raising=False)
    assert _build_auth() is None


def test_domain_alone_still_builds_authkit(monkeypatch):
    """Without the MCP client vars, today's remote-AS world is unchanged."""
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", DOMAIN)
    monkeypatch.setenv("RIF_BASE_URL", BASE)
    monkeypatch.delenv("WORKOS_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("WORKOS_MCP_CLIENT_SECRET", raising=False)
    assert isinstance(_build_auth(), AuthKitProvider)


def test_partial_proxy_config_refuses_to_boot(monkeypatch):
    """Half a proxy config must be a crash, not a silent downgrade.

    An operator who set the client id but forgot the signing key would
    otherwise deploy the old AuthKit boundary while believing the proxy
    is live -- the worst failure mode this branch can have.
    """
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", DOMAIN)
    monkeypatch.setenv("RIF_BASE_URL", BASE)
    monkeypatch.setenv("WORKOS_MCP_CLIENT_ID", "client_mcp_123")
    monkeypatch.delenv("WORKOS_MCP_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(get_settings(), "jwt_signing_key", "")
    monkeypatch.setattr(get_settings(), "oauth_store_dir", "")
    with pytest.raises(RuntimeError, match="WORKOS_MCP_CLIENT_SECRET"):
        _build_auth()


def test_full_config_builds_the_proxy(proxy_env):
    """The complete var set selects WorkOSProvider with our choices wired in."""
    provider = _build_auth()
    assert isinstance(provider, WorkOSProvider)
    # Private attrs, pinned by uv.lock: cheap insurance that the two
    # security-relevant constructor args actually arrived.
    assert provider._require_authorization_consent == "remember"


def test_disallowed_redirect_uri_is_refused(proxy_env):
    """The default allowlist rejects an arbitrary origin and accepts Claude's.

    This is the enforcement point the review flagged: exercising
    ``_build_auth``'s wiring end-to-end through a real ``/authorize`` request
    is heavy (DCR, PKCE, a real upstream redirect), so instead this drives
    the actual allowlist the provider carries -- the same list FastMCP's
    proxy consults on every redirect -- through the validator it uses.
    """
    provider = _build_auth()
    allowed = provider._allowed_client_redirect_uris
    assert allowed == _DEFAULT_CLIENT_REDIRECTS
    assert not validate_redirect_uri(
        redirect_uri="https://evil.example/cb", allowed_patterns=allowed
    )
    assert validate_redirect_uri(
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        allowed_patterns=allowed,
    )


def test_custom_allowed_redirects_replace_the_default(monkeypatch, proxy_env):
    """RIF_ALLOWED_CLIENT_REDIRECTS, when set, fully replaces the default list."""
    monkeypatch.setattr(
        get_settings(), "allowed_client_redirects", "https://only.example/*"
    )
    provider = _build_auth()
    assert provider._allowed_client_redirect_uris == ["https://only.example/*"]


async def test_metadata_names_reef_as_authorization_server(proxy_env):
    """The protected-resource metadata now points clients at reef itself.

    This is the observable cutover: before, authorization_servers named
    the AuthKit domain; after, it names RIF_BASE_URL, so clients register
    and authorize against reef.
    """
    server = FastMCP("probe", auth=_build_auth())
    transport = httpx.ASGITransport(app=server.http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    servers = response.json()["authorization_servers"]
    assert any(BASE in s for s in servers)
    assert not any(DOMAIN in s for s in servers)
