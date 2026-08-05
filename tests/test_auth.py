import pytest

from rif.access import AccessDenied
from rif.auth import principal_from_claims


async def test_known_subject_resolves(session, household):
    household["wouter"].subject = "auth0|abc123"
    await session.flush()
    principal = await principal_from_claims(session, {"sub": "auth0|abc123"})
    assert principal.person_id == household["wouter"].id


async def test_first_login_binds_subject_via_verified_email(session, household):
    claims = {"sub": "auth0|new", "email": "wouter@example.test", "email_verified": True}
    principal = await principal_from_claims(session, claims)
    assert principal.person_id == household["wouter"].id
    assert household["wouter"].subject == "auth0|new"


async def test_unverified_email_cannot_bind(session, household):
    with pytest.raises(AccessDenied):
        await principal_from_claims(
            session, {"sub": "auth0|x", "email": "wouter@example.test",
                      "email_verified": False})


async def test_stranger_is_denied(session, household):
    with pytest.raises(AccessDenied):
        await principal_from_claims(
            session, {"sub": "auth0|y", "email": "stranger@example.test",
                      "email_verified": True})


def test_http_transport_refuses_to_start_without_auth(monkeypatch):
    """PORT set with no auth provider must abort at startup, not boot open.

    The deployed endpoint is never allowed to be reachable without
    authentication; a missing WORKOS_AUTHKIT_DOMAIN/RIF_BASE_URL has to be
    a loud startup failure rather than a silently unauthenticated server.
    """
    from rif import server

    monkeypatch.setenv("PORT", "8080")
    assert server.mcp.auth is None  # the test env never sets the WorkOS vars
    with pytest.raises(RuntimeError, match="auth"):
        server.main()
