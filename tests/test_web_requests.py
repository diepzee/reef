"""Request-level auth: cookie -> principal, CSRF guard, dev fallback."""

from uuid import uuid4

import pytest
from starlette.requests import Request
from starlette.responses import Response

from reef.access import Principal
from reef.config import get_settings
from reef.web.requests import (
    CsrfRejected,
    Unauthenticated,
    cookie_secure,
    principal_from_request,
    require_csrf,
    set_session_cookie,
)
from reef.web.session import seal


def _request(headers: dict[str, str] | None = None, method: str = "GET") -> Request:
    """Construct a bare Starlette request from headers and method."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": method, "headers": raw, "path": "/api/x"}
    return Request(scope)


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    """Inject test session secret and disable dev mode."""
    monkeypatch.setattr(get_settings(), "session_secret", "test-secret")
    monkeypatch.delenv("REEF_DEV_INSECURE", raising=False)


def test_valid_cookie_yields_principal():
    """Unsealing a valid cookie yields the principal."""
    pid = uuid4()
    token = seal(pid, "a@b.com", secret="test-secret")
    principal = principal_from_request(_request({"cookie": f"rif_session={token}"}))
    assert principal == Principal(person_id=pid, email="a@b.com")


def test_missing_cookie_raises():
    """Missing cookie raises Unauthenticated."""
    with pytest.raises(Unauthenticated):
        principal_from_request(_request())


def test_bad_cookie_raises():
    """Malformed token raises Unauthenticated."""
    with pytest.raises(Unauthenticated):
        principal_from_request(_request({"cookie": "rif_session=junk"}))


def test_csrf_required_on_mutation():
    """CSRF header required for mutations; not for reads."""
    with pytest.raises(CsrfRejected):
        require_csrf(_request(method="PUT"))
    require_csrf(_request({"x-reef-csrf": "1"}, method="PUT"))  # no raise
    require_csrf(_request(method="GET"))  # reads never need it


def test_cookie_secure_default_true():
    """Secure is on by default -- it must not depend on request scheme.

    Regression test: callers used to derive ``secure`` from
    ``request.url.scheme``, which is always ``"http"`` behind Railway's
    TLS-terminating proxy.
    """
    assert cookie_secure() is True


def test_cookie_secure_false_with_dev_insecure(monkeypatch):
    """REEF_DEV_INSECURE=1 is the only thing that turns Secure off."""
    monkeypatch.setenv("REEF_DEV_INSECURE", "1")
    assert cookie_secure() is False


def test_set_session_cookie():
    """Setting a session cookie seals the token and sets flags."""
    response = Response()
    set_session_cookie(
        response, Principal(person_id=uuid4(), email="a@b.com"), secure=True
    )
    header = response.headers["set-cookie"]
    assert "rif_session=" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.lower() or "samesite=lax" in header.lower()
    assert "Secure" in header
