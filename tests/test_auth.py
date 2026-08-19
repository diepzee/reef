import pytest

from reef.access import AccessDenied
from reef.auth import current_principal, principal_from_claims
from reef.identity import person_by_subject


async def test_known_subject_resolves(tx, household, graph):
    await graph.bind_subject(household["wouter"], "auth0|abc123")
    principal = await principal_from_claims({"sub": "auth0|abc123"})
    assert principal.person_id == household["wouter"].id


async def test_first_login_binds_subject_via_verified_email(tx, household):
    claims = {
        "sub": "auth0|new",
        "email": "wouter@example.test",
        "email_verified": True,
    }
    principal = await principal_from_claims(claims)
    assert principal.person_id == household["wouter"].id
    # Read back through the binding lookup rather than the row: persons is
    # self-only now, and nothing here is armed.
    bound = await person_by_subject("auth0|new")
    assert bound is not None and bound.person_id == household["wouter"].id


async def test_unverified_email_cannot_bind(tx, household):
    with pytest.raises(AccessDenied):
        await principal_from_claims(
            {"sub": "auth0|x", "email": "wouter@example.test", "email_verified": False}
        )


async def test_only_a_boolean_true_counts_as_a_verified_email(tx, household):
    """Truthy stand-ins for the flag must not bind; the check has to be exact.

    ``email_verified`` is the entire binding mechanism for an unbound person
    row, and providers differ in how they serialize it. ``not "false"`` is
    ``False``, so a truthiness test would let an unverified address claim
    someone else's pending invite — permanently, since binding is one-way.
    """
    for value in ("false", "False", "0", 1, "true"):
        with pytest.raises(AccessDenied):
            await principal_from_claims(
                {
                    "sub": "auth0|z",
                    "email": "wouter@example.test",
                    "email_verified": value,
                }
            )
    # None of those attempts may have bound anything.
    for value in ("auth0|z",):
        assert await person_by_subject(value) is None


async def test_first_bind_onboards_a_personal_space(tx, household):
    from reef.access import Principal
    from reef.pages import get_page
    from reef.spaces import invite

    owner = Principal(person_id=household["wouter"].id, email=household["wouter"].email)
    await invite(owner, "household", "anna@example.test", display_name="Anna")
    claims = {"sub": "auth0|anna", "email": "anna@example.test", "email_verified": True}
    principal = await principal_from_claims(claims)
    persona = await get_page(principal, "personal", "meta/persona.md")
    assert persona is not None
    # The protocol ships with the product; it is no longer seeded as a page.
    assert await get_page(principal, "personal", "meta/protocol.md") is None


async def test_stranger_is_denied(tx, household):
    with pytest.raises(AccessDenied):
        await principal_from_claims(
            {"sub": "auth0|y", "email": "stranger@example.test", "email_verified": True}
        )


async def test_http_without_token_is_denied_by_default(tx, monkeypatch):
    """A tokenless HTTP connection is refused, not treated as stdio dev mode.

    ``get_access_token()`` returns ``None`` whenever the server runs HTTP
    without an auth provider wired up (the ``REEF_DEV_INSECURE=1`` local-dev
    path). Without the flag set, that must raise ``AccessDenied`` -- never
    fall through to the dev-email lookup, and never surface the unguarded
    ``AttributeError`` from ``None.claims``.
    """
    import fastmcp.server.dependencies

    monkeypatch.setenv("PORT", "8080")
    monkeypatch.delenv("REEF_DEV_INSECURE", raising=False)
    monkeypatch.setattr(fastmcp.server.dependencies, "get_access_token", lambda: None)
    with pytest.raises(AccessDenied):
        await current_principal()


async def test_http_without_token_falls_back_when_insecure(tx, household, monkeypatch):
    """``REEF_DEV_INSECURE=1`` makes a tokenless HTTP connection behave like stdio.

    This is the local-dev path: MCP-over-HTTP with no auth provider wired
    up still needs to resolve a principal, so it falls through to the same
    ``REEF_DEV_PRINCIPAL_EMAIL`` lookup stdio mode uses.
    """
    import fastmcp.server.dependencies

    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("REEF_DEV_INSECURE", "1")
    monkeypatch.setenv("REEF_DEV_PRINCIPAL_EMAIL", household["wouter"].email)
    monkeypatch.setattr(fastmcp.server.dependencies, "get_access_token", lambda: None)
    principal = await current_principal()
    assert principal.person_id == household["wouter"].id


async def test_http_insecure_fallback_denies_unknown_email(tx, household, monkeypatch):
    """The insecure-HTTP fallback still gates on the persons table.

    Falling through to the dev-email lookup does not mean any email is
    accepted -- an email nobody seeded is denied exactly as stdio mode
    denies it.
    """
    import fastmcp.server.dependencies

    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("REEF_DEV_INSECURE", "1")
    monkeypatch.setenv("REEF_DEV_PRINCIPAL_EMAIL", "stranger@example.test")
    monkeypatch.setattr(fastmcp.server.dependencies, "get_access_token", lambda: None)
    with pytest.raises(AccessDenied):
        await current_principal()


def test_http_transport_refuses_to_start_without_auth(monkeypatch):
    """PORT set with no auth provider must abort at startup, not boot open.

    The deployed endpoint is never allowed to be reachable without
    authentication; a missing WORKOS_AUTHKIT_DOMAIN/REEF_BASE_URL has to be
    a loud startup failure rather than a silently unauthenticated server.
    """
    from reef import server

    monkeypatch.setenv("PORT", "8080")
    assert server.mcp.auth is None  # the test env never sets the WorkOS vars
    with pytest.raises(RuntimeError, match="auth"):
        server.main()
