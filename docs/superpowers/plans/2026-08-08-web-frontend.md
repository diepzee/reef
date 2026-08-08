# Web Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A browser UI for rif — members see their spaces and pages, edit pages in a minimal markdown editor, and owners administer spaces (create, invite, remove) — served by the existing FastMCP service.

**Architecture:** A Bun + React + TypeScript SPA in `frontend/`, built to static files and served at `/app/*` by the existing Starlette/FastMCP app, which also gains a JSON API at `/api/*` (FastMCP custom routes). API handlers are thin wrappers over the existing domain functions (`pages.py`, `spaces.py`, `context.py`, `attachments.py`), so RLS and the invitation-only allowlist apply unchanged. Browser auth is a parallel AuthKit flow (authorization-code + PKCE) that resolves through the existing `principal_from_claims` and sets a signed session cookie.

**Tech Stack:** Python 3.13, FastMCP 2 (Starlette), Piccolo/Postgres+RLS, httpx (already present transitively; used for OIDC calls), stdlib `hmac` for cookie signing. Frontend: Bun (package manager/dev server/bundler/test runner), React 19, react-router-dom, markdown-it, DOMPurify. No Node, no Vite.

## Global Constraints

- Python ≥3.13, modern types, mandatory ReST docstrings (no types in docstrings — that's what hints are for).
- The app role is `rif_app` (no DDL); never bypass RLS; every domain call happens inside `transaction_scope()` with a `Principal`.
- `meta/` pages: no write path from the web API (`allow_protected` is never set).
- Spec: `docs/superpowers/specs/2026-08-08-web-frontend-design.md`. Where this plan and the spec disagree, the spec wins.
- Backend tests run against real Postgres: `uv run pytest` (needs `rif_test` DB on localhost:5433, `docker compose up -d`).
- Frontend: Bun only. `cd frontend && bun install && bun test && bun run build`.
- Commit style: `feat:`/`docs:`/`test:` prefixes, imperative mood.
- New env vars introduced here: `WORKOS_CLIENT_ID` (os.environ, like `WORKOS_AUTHKIT_DOMAIN`), `RIF_SESSION_SECRET` (Settings), `RIF_DEV_INSECURE` (dev only).
- Error mapping (used by every API task): `AccessDenied` → 404 `{"error": "not_found"}`; `SpaceError` → 400 `{"error": "space_error", "detail": str(e)}`; `VersionConflict` → 409 `{"error": "version_conflict"}`; `ProtectedPath` → 403 `{"error": "protected"}`; no session → 401 `{"error": "unauthenticated"}`; missing CSRF header on mutation → 403 `{"error": "csrf"}`.
- Mutating requests require header `X-Rif-Csrf: 1`.
- Session cookie name `rif_session`; OAuth transaction cookie `rif_oauth`; both HttpOnly, SameSite=Lax, Secure when the request is https.

---

### Task 1: Session sealing

Signed, expiring session tokens — stdlib HMAC, no new dependency.

**Files:**
- Create: `src/rif/web/__init__.py` (empty, one-line docstring `"""Browser-facing web surface: session auth, JSON API, static SPA."""`)
- Create: `src/rif/web/session.py`
- Test: `tests/test_web_session.py`

**Interfaces:**
- Produces: `seal(person_id: UUID, email: str, *, secret: str, now: float | None = None, ttl_seconds: int = SESSION_TTL_SECONDS) -> str` and `unseal(token: str, *, secret: str, now: float | None = None) -> SessionData | None` where `SessionData` is a frozen dataclass with `person_id: UUID`, `email: str`, `expires_at: float`. `SESSION_TTL_SECONDS = 7 * 24 * 3600`.

- [ ] **Step 1: Write the failing tests**

```python
"""Session cookie sealing: round-trip, tamper, expiry."""

from uuid import uuid4

from rif.web.session import SESSION_TTL_SECONDS, seal, unseal

SECRET = "test-secret"


def test_round_trip():
    pid = uuid4()
    token = seal(pid, "a@b.com", secret=SECRET, now=1000.0)
    data = unseal(token, secret=SECRET, now=1000.0)
    assert data is not None
    assert data.person_id == pid
    assert data.email == "a@b.com"
    assert data.expires_at == 1000.0 + SESSION_TTL_SECONDS


def test_tampered_signature_rejected():
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    payload, sig = token.rsplit(".", 1)
    assert unseal(payload + "." + "x" * len(sig), secret=SECRET, now=1000.0) is None


def test_tampered_payload_rejected():
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    _, sig = token.rsplit(".", 1)
    other = seal(uuid4(), "evil@b.com", secret=SECRET, now=1000.0)
    payload, _ = other.rsplit(".", 1)
    assert unseal(payload + "." + sig, secret=SECRET, now=1000.0) is None


def test_wrong_secret_rejected():
    token = seal(uuid4(), "a@b.com", secret=SECRET)
    assert unseal(token, secret="other") is None


def test_expired_rejected():
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    assert unseal(token, secret=SECRET, now=1000.0 + SESSION_TTL_SECONDS + 1) is None


def test_garbage_rejected():
    assert unseal("not-a-token", secret=SECRET) is None
    assert unseal("", secret=SECRET) is None
    assert unseal("a.b.c.d", secret=SECRET) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rif.web'`

- [ ] **Step 3: Implement**

```python
"""Signed session tokens for the browser surface.

Format: ``b64url(json payload) . b64url(hmac_sha256(secret, payload))``.
Stdlib only: the token is a MAC over a tiny JSON document, which is all a
session cookie needs — no encryption (contents are non-secret), no new
dependency to vet.
"""

import base64
import hmac
import json
import time
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

SESSION_TTL_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class SessionData:
    """The verified contents of a session token."""

    person_id: UUID
    email: str
    expires_at: float


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload, sha256).digest())


def seal(
    person_id: UUID,
    email: str,
    *,
    secret: str,
    now: float | None = None,
    ttl_seconds: int = SESSION_TTL_SECONDS,
) -> str:
    """Produce a signed session token.

    :param person_id: the person's id
    :param email: the person's email
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :param ttl_seconds: lifetime from now
    :returns: the token string
    """
    issued = time.time() if now is None else now
    payload = json.dumps(
        {"pid": str(person_id), "email": email, "exp": issued + ttl_seconds},
        separators=(",", ":"),
    ).encode()
    return f"{_b64(payload)}.{_sign(payload, secret)}"


def unseal(token: str, *, secret: str, now: float | None = None) -> SessionData | None:
    """Verify a token and return its contents, or None.

    None for any defect — bad format, bad signature, expired — because the
    caller's only decision is "session or no session".

    :param token: the token string
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :returns: the session data, or None
    """
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        return None
    try:
        payload = _unb64(parts[0])
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(_sign(payload, secret), parts[1]):
        return None
    try:
        doc = json.loads(payload)
        data = SessionData(UUID(doc["pid"]), doc["email"], float(doc["exp"]))
    except (ValueError, KeyError, TypeError):
        return None
    current = time.time() if now is None else now
    if current >= data.expires_at:
        return None
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_session.py -v` — Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/rif/web/ tests/test_web_session.py
git commit -m "feat: signed session tokens for the web surface"
```

---

### Task 2: Request auth — principal resolution, CSRF, config

**Files:**
- Create: `src/rif/web/requests.py`
- Modify: `src/rif/config.py` (add two Settings fields)
- Test: `tests/test_web_requests.py`

**Interfaces:**
- Consumes: `seal`/`unseal` from Task 1; `Principal` from `rif.access`.
- Produces:
  - `Settings.session_secret: str = ""` and `Settings.static_dir: str = "frontend/dist"` (env `RIF_SESSION_SECRET`, `RIF_STATIC_DIR`).
  - `class Unauthenticated(Exception)`, `class CsrfRejected(Exception)` in `rif.web.requests`.
  - `principal_from_request(request: Request) -> Principal` — raises `Unauthenticated`.
  - `require_csrf(request: Request) -> None` — raises `CsrfRejected` for mutating methods without `X-Rif-Csrf: 1`.
  - `set_session_cookie(response: Response, principal: Principal, *, secure: bool) -> None` — seals and sets `rif_session` with max-age `SESSION_TTL_SECONDS` (this is the sliding renewal: callers re-set on every authenticated response).

- [ ] **Step 1: Write the failing tests**

```python
"""Request-level auth: cookie -> principal, CSRF guard, dev fallback."""

from uuid import uuid4

import pytest
from starlette.requests import Request
from starlette.responses import Response

from rif.access import Principal
from rif.config import get_settings
from rif.web.requests import (
    CsrfRejected,
    Unauthenticated,
    principal_from_request,
    require_csrf,
    set_session_cookie,
)
from rif.web.session import seal


def _request(headers: dict[str, str] | None = None, method: str = "GET") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "method": method, "headers": raw, "path": "/api/x"}
    return Request(scope)


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "session_secret", "test-secret")
    monkeypatch.delenv("RIF_DEV_INSECURE", raising=False)


def test_valid_cookie_yields_principal():
    pid = uuid4()
    token = seal(pid, "a@b.com", secret="test-secret")
    principal = principal_from_request(_request({"cookie": f"rif_session={token}"}))
    assert principal == Principal(person_id=pid, email="a@b.com")


def test_missing_cookie_raises():
    with pytest.raises(Unauthenticated):
        principal_from_request(_request())


def test_bad_cookie_raises():
    with pytest.raises(Unauthenticated):
        principal_from_request(_request({"cookie": "rif_session=junk"}))


def test_csrf_required_on_mutation():
    with pytest.raises(CsrfRejected):
        require_csrf(_request(method="PUT"))
    require_csrf(_request({"x-rif-csrf": "1"}, method="PUT"))  # no raise
    require_csrf(_request(method="GET"))  # reads never need it


def test_set_session_cookie():
    response = Response()
    set_session_cookie(
        response, Principal(person_id=uuid4(), email="a@b.com"), secure=True
    )
    header = response.headers["set-cookie"]
    assert "rif_session=" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.lower() or "samesite=lax" in header.lower()
    assert "Secure" in header
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_requests.py -v`
Expected: FAIL with `ModuleNotFoundError` (rif.web.requests)

- [ ] **Step 3: Add the Settings fields**

In `src/rif/config.py`, inside `Settings`, after `signed_url_ttl_seconds`:

```python
    session_secret: str = ""
    static_dir: str = "frontend/dist"
```

- [ ] **Step 4: Implement `src/rif/web/requests.py`**

```python
"""Request-level auth for the web surface: cookie to principal, CSRF."""

import os

from starlette.requests import Request
from starlette.responses import Response

from rif.access import Principal
from rif.config import get_settings
from rif.web.session import SESSION_TTL_SECONDS, seal, unseal

SESSION_COOKIE = "rif_session"
CSRF_HEADER = "x-rif-csrf"
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class Unauthenticated(Exception):
    """No valid session on the request."""


class CsrfRejected(Exception):
    """A mutating request without the CSRF header."""


def principal_from_request(request: Request) -> Principal:
    """Resolve the principal from the session cookie.

    Dev fallback: with ``RIF_DEV_INSECURE=1`` and ``RIF_DEV_PRINCIPAL_EMAIL``
    set, an anonymous request resolves to the dev person — mirroring the
    stdio fallback in ``rif.auth`` and equally dead in production, where
    neither variable is set.

    :param request: the incoming request
    :raises Unauthenticated: when no valid session exists
    :returns: the authenticated principal
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        data = unseal(token, secret=get_settings().session_secret)
        if data is not None:
            return Principal(person_id=data.person_id, email=data.email)
    if os.environ.get("RIF_DEV_INSECURE") == "1":
        email = os.environ.get("RIF_DEV_PRINCIPAL_EMAIL")
        if email:
            # Local dev only: person lookup happens in the handler's
            # transaction via principal_for_dev_email below.
            raise _DevFallback(email)
    raise Unauthenticated


class _DevFallback(Exception):
    """Internal: carries the dev email to the handler wrapper."""

    def __init__(self, email: str) -> None:
        self.email = email


def require_csrf(request: Request) -> None:
    """Reject mutating requests that lack the CSRF header.

    :param request: the incoming request
    :raises CsrfRejected: for a mutation without ``X-Rif-Csrf: 1``
    """
    if request.method in _MUTATING and request.headers.get(CSRF_HEADER) != "1":
        raise CsrfRejected


def set_session_cookie(
    response: Response, principal: Principal, *, secure: bool
) -> None:
    """Seal a fresh 7-day session onto the response — the sliding renewal.

    :param response: the response to set the cookie on
    :param principal: the authenticated person
    :param secure: whether to mark the cookie Secure (https requests)
    """
    token = seal(
        principal.person_id, principal.email, secret=get_settings().session_secret
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
```

Note: the `_DevFallback` mechanics get consumed in Task 4's handler wrapper (it resolves the email to a `Person` row inside the transaction). The tests above monkeypatch `RIF_DEV_INSECURE` away, so they exercise only the production path.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_requests.py -v` — Expected: 5 PASS
Also run: `uv run pytest` — full suite still green (config change is additive).

- [ ] **Step 6: Commit**

```bash
git add src/rif/web/requests.py src/rif/config.py tests/test_web_requests.py
git commit -m "feat: web request auth — session cookie to principal, CSRF guard"
```

---

### Task 3: OIDC login flow — /api/auth/login, /callback, /logout

**Files:**
- Create: `src/rif/web/oidc.py`
- Create: `src/rif/web/routes_auth.py`
- Test: `tests/test_web_auth_routes.py`

**Interfaces:**
- Consumes: `seal`/`unseal`, `set_session_cookie`; `principal_from_claims` from `rif.auth`; `transaction_scope` from `rif.db`.
- Produces:
  - `class OIDCClient(Protocol)` with `async def exchange(self, code: str, verifier: str, redirect_uri: str) -> str` (returns access token; raises `OIDCError` on failure) and `async def userinfo(self, access_token: str) -> dict`.
  - `class AuthKitOIDC` implementing it with httpx against `{WORKOS_AUTHKIT_DOMAIN}/oauth2/token` and `/oauth2/userinfo`, using `WORKOS_CLIENT_ID`.
  - `class OIDCError(Exception)`.
  - `register_auth_routes(mcp, client_factory: Callable[[], OIDCClient] = AuthKitOIDC) -> None` registering GET `/api/auth/login`, GET `/api/auth/callback`, POST `/api/auth/logout`.

Flow details (each is a plain Starlette handler registered via `mcp.custom_route(path, methods=[...])(handler)`):

- **login**: generate `state` (32 hex chars via `secrets.token_hex(16)`) and PKCE `verifier` (`secrets.token_urlsafe(64)`); challenge = `b64url(sha256(verifier))` (no padding), method S256. Store `{"state": state, "verifier": verifier}` as a sealed 10-minute cookie `rif_oauth` (reuse `seal`? No — that seals person sessions. Add in `oidc.py`: `_seal_oauth(state, verifier, secret)` / `_unseal_oauth(token, secret)` using the same `hmac` pattern with payload `{"st":…, "vf":…, "exp": now+600}`; 15 lines, keeps session.py single-purpose). Redirect (302) to `{domain}/oauth2/authorize?response_type=code&client_id={WORKOS_CLIENT_ID}&redirect_uri={RIF_BASE_URL}/api/auth/callback&state={state}&code_challenge={challenge}&code_challenge_method=S256&scope=openid+profile+email`. `WORKOS_AUTHKIT_DOMAIN` may lack the scheme — normalize with `https://` prefix if missing (mirror `_build_auth`). If `WORKOS_AUTHKIT_DOMAIN`/`WORKOS_CLIENT_ID`/`RIF_BASE_URL` unset → 503 `{"error": "auth_unconfigured"}`.
- **callback**: query `code` + `state`; unseal `rif_oauth`, compare state (`hmac.compare_digest`); mismatch/missing → 400 `{"error": "oauth_state"}`. Exchange code, fetch userinfo, then `async with transaction_scope(): principal = await principal_from_claims(claims)`. `AccessDenied` → 403 HTML page (plain string: "This email isn't a member of any rif space. Ask the person who runs your space to invite you."). Success → `RedirectResponse("/app", status_code=303)` with session cookie set (`secure=request.url.scheme == "https"`) and `rif_oauth` deleted.
- **logout**: delete `rif_session` cookie, return `{"ok": true}`. (POST — CSRF header required via `require_csrf`.)

`AuthKitOIDC.exchange` posts `grant_type=authorization_code, code, client_id, code_verifier, redirect_uri` as form data; non-200 or missing `access_token` → `OIDCError`. `userinfo` GETs with `Authorization: Bearer`; non-200 → `OIDCError`. Callback maps `OIDCError` → 502 `{"error": "oidc_upstream"}`.

- [ ] **Step 1: Write the failing tests**

Use httpx's ASGI transport against `mcp.http_app()`. Test module needs the `tx`-free style (handlers own their transactions) plus the `Graph` builders from conftest.

```python
"""Auth route flow with a fake OIDC upstream."""

import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio

from rif.server import mcp
from rif.web.routes_auth import register_auth_routes


class FakeOIDC:
    def __init__(self, claims: dict):
        self.claims = claims
        self.exchanged: list[tuple[str, str, str]] = []

    async def exchange(self, code, verifier, redirect_uri):
        self.exchanged.append((code, verifier, redirect_uri))
        return "fake-access-token"

    async def userinfo(self, access_token):
        assert access_token == "fake-access-token"
        return self.claims


@pytest_asyncio.fixture
async def web(monkeypatch, graph):
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
    client, fake, _ = web
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
    client, fake, person = web
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
    client, _, _ = web
    await client.get("/api/auth/login")
    response = await client.get(
        "/api/auth/callback", params={"code": "c", "state": "wrong"}
    )
    assert response.status_code == 400


async def test_callback_unknown_email_gets_403(web):
    client, fake, _ = web
    fake.claims = {"sub": "sub_2", "email": "stranger@x.com", "email_verified": True}
    login = await client.get("/api/auth/login")
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    response = await client.get(
        "/api/auth/callback", params={"code": "c", "state": state}
    )
    assert response.status_code == 403
```

A `graph` fixture may not exist yet in conftest — check; `tests/conftest.py` defines `class Graph` and (verify) a fixture. If there is no `graph` fixture, add one to `tests/conftest.py`:

```python
@pytest_asyncio.fixture
def graph():
    """Topology builders for tests that assemble their own worlds."""
    return Graph()
```

(Grep first: `grep -n "def graph" tests/conftest.py` — several existing tests already build topologies; reuse whatever fixture name they use.)

Registration idempotence: `register_auth_routes` will be called once at import in production (Task 6) but repeatedly across tests. Guard it: module-level `_registered = False`, return early when already registered against the same `mcp`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_auth_routes.py -v`
Expected: FAIL with `ModuleNotFoundError` (rif.web.routes_auth)

- [ ] **Step 3: Implement `oidc.py` and `routes_auth.py`** per the interface block above. `oidc.py` sketch:

```python
"""OIDC against the AuthKit domain: code exchange and userinfo."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import httpx


class OIDCError(Exception):
    """The upstream token or userinfo call failed."""


def authkit_domain() -> str:
    """Return the AuthKit base URL with an https scheme.

    :returns: e.g. ``https://foo.authkit.app``
    """
    domain = os.environ.get("WORKOS_AUTHKIT_DOMAIN", "")
    if domain and not domain.startswith("http"):
        domain = f"https://{domain}"
    return domain.rstrip("/")


def pkce_pair() -> tuple[str, str]:
    """Return a fresh (verifier, challenge) PKCE pair.

    :returns: verifier and its S256 challenge
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class AuthKitOIDC:
    """The real OIDC client; tests substitute a fake via the protocol."""

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> str:
        """Exchange an authorization code for an access token.

        :param code: the authorization code
        :param verifier: the PKCE verifier from the login step
        :param redirect_uri: must match the authorize request exactly
        :raises OIDCError: on any upstream failure
        :returns: the access token
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{authkit_domain()}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": os.environ.get("WORKOS_CLIENT_ID", ""),
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                },
            )
        if response.status_code != 200:
            raise OIDCError(f"token endpoint returned {response.status_code}")
        token = response.json().get("access_token")
        if not token:
            raise OIDCError("token response carried no access_token")
        return token

    async def userinfo(self, access_token: str) -> dict:
        """Fetch the OIDC userinfo claims.

        :param access_token: the bearer token from :meth:`exchange`
        :raises OIDCError: on any upstream failure
        :returns: the claims document
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{authkit_domain()}/oauth2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 200:
            raise OIDCError(f"userinfo returned {response.status_code}")
        return response.json()
```

Plus `_seal_oauth(state, verifier, *, secret, now=None)` / `_unseal_oauth(token, *, secret, now=None)` — same HMAC pattern as `session.py`, payload `{"st", "vf", "exp": now + 600}`, returns `tuple[str, str] | None`. `routes_auth.py` implements the three handlers exactly per the flow details above; also add `httpx` to `[dependency-groups].dev` and main dependencies in `pyproject.toml` (it is already in the lock via fastmcp; declaring it makes the direct use honest) and run `uv lock` then `uv sync`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_auth_routes.py -v` — Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/rif/web/oidc.py src/rif/web/routes_auth.py tests/ pyproject.toml uv.lock
git commit -m "feat: browser login via AuthKit OIDC code+PKCE flow"
```

---

### Task 4: JSON API — handler wrapper and read endpoints

**Files:**
- Create: `src/rif/web/routes_api.py`
- Test: `tests/test_web_api_read.py`

**Interfaces:**
- Consumes: `principal_from_request`, `require_csrf`, `set_session_cookie`, exceptions from Task 2; `build_index` (`rif.context`), `get_page` (`rif.pages`), `get_attachment`, `S3ObjectStore` (`rif.attachments`), `member_names`, `resolve_space` (`rif.access`), `transaction_scope`.
- Produces:
  - `api(handler)` — an async decorator used by every endpoint: opens `transaction_scope()`, resolves the principal (consuming `_DevFallback` by looking up `Person` by email inside the transaction), calls `require_csrf`, invokes `handler(request, principal) -> Response | dict` (a dict becomes `JSONResponse`), maps the Global Constraints error table onto responses, and on success calls `set_session_cookie(response, principal, secure=request.url.scheme == "https")`.
  - `register_api_routes(mcp) -> None` (idempotent, like Task 3) registering all `/api/*` data endpoints (this task: the reads; Task 5 adds writes to the same module).
  - Read endpoints: `GET /api/me` → `{"person_id", "email", "display_name"}` (display_name fetched from `Person`); `GET /api/index` → `asdict(await build_index(principal))`; `GET /api/pages/{space}/{path:path}` → `{"space", "path", "title", "tags", "body", "version", "updated"}` or 404; `GET /api/images/{space}/{key:path}` → 302 redirect to `S3ObjectStore().signed_url(key, get_settings().signed_url_ttl_seconds)` after `get_attachment` confirms visibility, else 404.

- [ ] **Step 1: Write the failing tests**

```python
"""Read API: membership slicing, page fetch, 401s."""

import httpx
import pytest_asyncio

from rif.pages import save_page
from rif.server import mcp
from rif.web.routes_api import register_api_routes


@pytest_asyncio.fixture
async def api(monkeypatch, graph):
    from rif.config import get_settings

    monkeypatch.setattr(get_settings(), "session_secret", "test-secret")
    register_api_routes(mcp)
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="https://rif.example"
    ) as client:
        yield client


def _login(client: httpx.AsyncClient, person) -> None:
    from rif.web.session import seal

    token = seal(person.id, person.email, secret="test-secret")
    client.cookies.set("rif_session", token)


@pytest_asyncio.fixture
async def world(graph, tx):
    """Two people; alice owns 'team' with bob; carol is elsewhere."""
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    await graph.personal_space(alice)
    await graph.personal_space(bob)
    team = await graph.shared_space("team", owner=alice, members=[bob])
    return alice, bob, team


async def test_unauthenticated_index_is_401(api):
    response = await api.get("/api/index")
    assert response.status_code == 401


async def test_index_is_sliced_per_person(api, world):
    alice, bob, _ = world
    _login(api, bob)
    response = await api.get("/api/index")
    assert response.status_code == 200
    aliases = {space["alias"] for space in response.json()["spaces"]}
    assert aliases == {"personal", "team"}


async def test_me(api, world):
    alice, _, _ = world
    _login(api, alice)
    body = (await api.get("/api/me")).json()
    assert body["email"] == "alice@x.com"
    assert body["display_name"] == "Alice"


async def test_get_page_and_404(api, world):
    from rif.access import Principal

    alice, bob, _ = world
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
```

The `world` fixture's `graph.personal_space` / `graph.shared_space` calls: **check `tests/conftest.py`'s `Graph` class for the real builder names and signatures** (read the class before writing; several existing tests construct spaces — mirror `tests/test_spaces.py`'s usage). Adjust the fixture to the actual API; the test bodies stand. Note the fixture also uses `tx` — but the API handlers open their own `transaction_scope()`; **seeding must commit before the request runs**, so do NOT use the `tx` fixture here; let each builder run outside a transaction (Piccolo queries are ambient) and drop `tx` from `world` and from the `save_page` call (wrap that one in its own `async with transaction_scope():`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_api_read.py -v`
Expected: FAIL with `ModuleNotFoundError` (rif.web.routes_api)

- [ ] **Step 3: Implement `routes_api.py`** per the Interfaces block. The `api` decorator shape:

```python
def api(handler):
    """Wrap a handler with transaction, auth, CSRF, errors, and renewal.

    :param handler: async ``(request, principal) -> Response | dict``
    :returns: a Starlette-compatible endpoint
    """

    async def endpoint(request: Request) -> Response:
        try:
            require_csrf(request)
        except CsrfRejected:
            return JSONResponse({"error": "csrf"}, status_code=403)
        try:
            async with transaction_scope():
                try:
                    principal = principal_from_request(request)
                except _DevFallback as fallback:
                    person = (
                        await Person.objects()
                        .where(Person.email == fallback.email)
                        .first()
                    )
                    if person is None:
                        raise Unauthenticated from None
                    principal = Principal(person_id=person.id, email=person.email)
                result = await handler(request, principal)
        except Unauthenticated:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        except AccessDenied:
            return JSONResponse({"error": "not_found"}, status_code=404)
        except VersionConflict:
            return JSONResponse({"error": "version_conflict"}, status_code=409)
        except ProtectedPath:
            return JSONResponse({"error": "protected"}, status_code=403)
        except SpaceError as error:
            return JSONResponse(
                {"error": "space_error", "detail": str(error)}, status_code=400
            )
        response = result if isinstance(result, Response) else JSONResponse(result)
        set_session_cookie(
            response, principal, secure=request.url.scheme == "https"
        )
        return response

    return endpoint
```

Timestamps serialize with `.isoformat()`. 404 for a `get_page` returning `None`: `return JSONResponse({"error": "not_found"}, status_code=404)` directly from the handler.

- [ ] **Step 4: Run tests, then the full suite**

Run: `uv run pytest tests/test_web_api_read.py -v` then `uv run pytest` — all green.

- [ ] **Step 5: Commit**

```bash
git add src/rif/web/routes_api.py tests/test_web_api_read.py
git commit -m "feat: web read API — me, index, page, image redirect"
```

---

### Task 5: JSON API — write and admin endpoints

**Files:**
- Modify: `src/rif/web/routes_api.py`
- Test: `tests/test_web_api_write.py`

**Interfaces:**
- Consumes: `api` wrapper from Task 4; `save_page` (`rif.pages`), `create_space`, `invite`, `remove_member`, `member_names` (`rif.spaces`), `resolve_space` (`rif.access`).
- Produces endpoints:
  - `PUT /api/pages/{space}/{path:path}` — JSON body `{"body": str, "message": str, "title": str | null, "tags": list[str] | null, "expected_version": int | null}` (`expected_version` null means create — pass `None` through). Returns the saved page shaped as in Task 4's GET.
  - `POST /api/spaces` — `{"slug": str}` → `{"alias", "slug"}`.
  - `GET /api/spaces/{space}/members` — resolve space, return `{"members": member_names(space.id), "owner_email": ..., "is_owner": bool}`; owner email via the space's `owner_person_id` → `Person` lookup (check the actual column name on `Space` in `src/rif/models.py` before writing — grep `owner` there).
  - `POST /api/spaces/{space}/invites` — `{"email": str, "display_name": str | null}` → the `invite(...)` dict verbatim (it contains the disclosure text the UI must show).
  - `DELETE /api/spaces/{space}/members/{email}` → the `remove_member(...)` dict.

- [ ] **Step 1: Write the failing tests**

```python
"""Write API: page saves with optimistic lock, space admin, authz."""

# Fixtures _login / api / world: import-or-copy the same helpers as
# tests/test_web_api_read.py (move shared ones into tests/conftest.py if
# duplication bothers — a `web_client` fixture there is acceptable).

CSRF = {"X-Rif-Csrf": "1"}


async def test_put_creates_then_conflicts(api, world):
    alice, bob, _ = world
    _login(api, alice)
    created = await api.put(
        "/api/pages/team/notes/a.md",
        json={"body": "First line.\n", "message": "create", "title": "A"},
        headers=CSRF,
    )
    assert created.status_code == 200
    assert created.json()["version"] == 1
    stale = await api.put(
        "/api/pages/team/notes/a.md",
        json={"body": "x", "message": "stale", "expected_version": 0},
        headers=CSRF,
    )
    assert stale.status_code == 409


async def test_put_without_csrf_header_is_403(api, world):
    alice, _, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/team/notes/a.md", json={"body": "x", "message": "m"}
    )
    assert response.status_code == 403


async def test_meta_pages_are_read_only(api, world):
    alice, _, _ = world
    _login(api, alice)
    response = await api.put(
        "/api/pages/personal/meta/protocol.md",
        json={"body": "x", "message": "m"},
        headers=CSRF,
    )
    assert response.status_code == 403


async def test_create_space_and_slug_taken(api, world):
    alice, _, _ = world
    _login(api, alice)
    ok = await api.post("/api/spaces", json={"slug": "trip"}, headers=CSRF)
    assert ok.status_code == 200
    dup = await api.post("/api/spaces", json={"slug": "trip"}, headers=CSRF)
    assert dup.status_code == 400


async def test_invite_and_members_and_remove(api, world):
    alice, bob, _ = world
    _login(api, alice)
    invited = await api.post(
        "/api/spaces/team/invites", json={"email": "New@X.com"}, headers=CSRF
    )
    assert invited.status_code == 200
    assert "disclosure" in invited.json()
    members = (await api.get("/api/spaces/team/members")).json()
    assert members["is_owner"] is True
    assert len(members["members"]) == 3
    removed = await api.delete("/api/spaces/team/members/new@x.com", headers=CSRF)
    assert removed.status_code == 200


async def test_non_owner_cannot_invite(api, world):
    _, bob, _ = world
    _login(api, bob)
    response = await api.post(
        "/api/spaces/team/invites", json={"email": "e@x.com"}, headers=CSRF
    )
    assert response.status_code == 400
```

(Exact status for non-owner: `invite` raises `SpaceError` → 400 per the error table. If reading `spaces.py` shows a different exception, keep the domain behavior and fix the expected code.)

- [ ] **Step 2: Run tests to verify they fail** — new endpoints 404/405.

- [ ] **Step 3: Implement the endpoints** in `routes_api.py`, each ~5 lines inside the `api` wrapper, JSON body via `await request.json()` with missing-key → 400 `{"error": "bad_request"}` (wrap `KeyError` handling: `body = await request.json()`; required fields checked explicitly).

- [ ] **Step 4: Run tests, then the full suite** — all green.

- [ ] **Step 5: Commit**

```bash
git add src/rif/web/routes_api.py tests/test_web_api_write.py tests/conftest.py
git commit -m "feat: web write API — page saves, space admin, invites"
```

---

### Task 6: Static SPA serving + server wiring + dev mode

**Files:**
- Create: `src/rif/web/static.py`
- Modify: `src/rif/server.py` (register web routes; relax the HTTP guard for dev)
- Test: `tests/test_web_static.py`

**Interfaces:**
- Consumes: `Settings.static_dir` (Task 2); `register_auth_routes` (Task 3); `register_api_routes` (Task 4/5).
- Produces: `register_static_routes(mcp) -> None` (idempotent) with:
  - `GET /` → 307 redirect to `/app`.
  - `GET /app` and `GET /app/{path:path}` → serve the file from `static_dir` when it exists (via `starlette.responses.FileResponse`), else `index.html` (SPA fallback). Path traversal guard: `full = (base / relative).resolve()`; serve only if `full.is_relative_to(base.resolve())`; anything else → the fallback. Missing `index.html` (frontend not built) → 503 plain text "frontend not built".

**server.py changes:**
1. After the `mcp = FastMCP(...)` block: 
```python
from rif.web.routes_api import register_api_routes
from rif.web.routes_auth import register_auth_routes
from rif.web.static import register_static_routes

register_auth_routes(mcp)
register_api_routes(mcp)
register_static_routes(mcp)
```
(Import placement at top of file with the other imports; calls right after `mcp` creation.)
2. In `main()`, the HTTP guard becomes: refuse only when `mcp.auth is None` **and** `os.environ.get("RIF_DEV_INSECURE") != "1"`; when the flag is set, log a loud warning line (`print`, matching the file's plainness) `"RIF_DEV_INSECURE=1 — serving HTTP without auth; local development only"`. Update the docstring accordingly.

- [ ] **Step 1: Write the failing tests**

```python
"""Static serving: SPA fallback, traversal guard, root redirect."""

import httpx
import pytest_asyncio

from rif.server import mcp  # importing server registers all web routes


@pytest_asyncio.fixture
async def static_client(monkeypatch, tmp_path):
    from rif.config import get_settings

    (tmp_path / "index.html").write_text("<!doctype html><title>rif</title>")
    (tmp_path / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(get_settings(), "static_dir", str(tmp_path))
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        yield client


async def test_root_redirects_to_app(static_client):
    response = await static_client.get("/")
    assert response.status_code == 307
    assert response.headers["location"] == "/app"


async def test_asset_served(static_client):
    response = await static_client.get("/app/app.js")
    assert response.status_code == 200
    assert "console" in response.text


async def test_spa_fallback(static_client):
    response = await static_client.get("/app/spaces/team/pages/notes.md")
    assert response.status_code == 200
    assert "<title>rif</title>" in response.text


async def test_traversal_blocked(static_client):
    response = await static_client.get("/app/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 200  # falls back to index, never the file
    assert "<title>rif</title>" in response.text
```

- [ ] **Step 2: Run to verify failure**, **Step 3: implement `static.py` + the `server.py` edits**, **Step 4: run the file then the full suite** (server.py now registers routes at import — the whole existing suite must stay green), **Step 5: commit**:

```bash
git add src/rif/web/static.py src/rif/server.py tests/test_web_static.py
git commit -m "feat: serve the SPA and register web routes on the MCP app"
```

---

### Task 7: Frontend scaffold — Bun project, API client, router shell

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/api.ts`, `frontend/src/types.ts`, `frontend/src/summary.ts`, `frontend/src/summary.test.ts`, `frontend/src/app.css`, `frontend/dev.ts`, `frontend/.gitignore` (`node_modules/`, `dist/`)

**Interfaces:**
- Produces (used by Tasks 8–10):
  - `types.ts`: `SpaceIndex {alias: string; version: number; pages: PageMeta[]; attachments: AttachmentMeta[]}`, `PageMeta {path; title; tags: string[]; description; updated; size; version: number}`, `AttachmentMeta {key; mime; description}`, `Page {space; path; title; tags: string[]; body; version: number; updated}`, `Me {person_id; email; display_name}`, `Members {members: string[]; owner_email: string; is_owner: boolean}`, `InviteResult {space; email; already_member: boolean; disclosure: string}`.
  - `api.ts`: `apiGet<T>(path: string): Promise<T>`, `apiSend<T>(method: string, path: string, body?: unknown): Promise<T>` — both `fetch` with `credentials: "same-origin"`; `apiSend` adds `"X-Rif-Csrf": "1"` and JSON headers; a 401 anywhere sets `location.href = "/api/auth/login"`; non-2xx throws `ApiError` with `status` and parsed `{error, detail}`. Exports `class ApiError extends Error { status: number; code: string; detail?: string }`.
  - `summary.ts`: `indexDescription(body: string): string` — first non-empty, non-`#`-starting line, trimmed to 200 chars, `""` if none (mirror of `_summary` in `src/rif/context.py:63`).
- Match the shapes to what the API actually returns (Task 4/5) — `build_index`'s payload has a top-level `spaces` list (see `IndexPayload` in `src/rif/context.py`; verify field names by reading it before writing `types.ts`).

Key files:

`package.json`:
```json
{
  "name": "rif-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "bun run dev.ts",
    "build": "bun build ./index.html --outdir dist --minify",
    "test": "bun test"
  },
  "dependencies": {
    "react": "^19",
    "react-dom": "^19",
    "react-router-dom": "^7",
    "markdown-it": "^14",
    "dompurify": "^3"
  },
  "devDependencies": {
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "@types/markdown-it": "^14",
    "typescript": "^5"
  }
}
```

`dev.ts` (dev server with API proxy to the locally running Python server):
```ts
import index from "./index.html";

const API = "http://localhost:8000";

Bun.serve({
  port: 3000,
  routes: { "/*": index },
  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      return fetch(new Request(`${API}${url.pathname}${url.search}`, request));
    }
    return new Response("not found", { status: 404 });
  },
});
console.log("dev server on http://localhost:3000 (API → :8000)");
```

`summary.test.ts`:
```ts
import { expect, test } from "bun:test";
import { indexDescription } from "./summary";

test("first prose line wins", () => {
  expect(indexDescription("# Title\n\nThe summary line.\nMore.")).toBe(
    "The summary line.",
  );
});

test("headings and blanks are skipped", () => {
  expect(indexDescription("# A\n## B\n\n")).toBe("");
});

test("trimmed to 200 chars", () => {
  expect(indexDescription("x".repeat(300)).length).toBe(200);
});
```

`App.tsx` router shell (views arrive in Tasks 9–10; stub them as `<p>…</p>` placeholders **only in this task**, replaced by real components in their own tasks):
```tsx
import { BrowserRouter, Route, Routes } from "react-router-dom";

export default function App() {
  return (
    <BrowserRouter basename="/app">
      <Routes>
        <Route path="/" element={<p>home</p>} />
        <Route path="/spaces/new" element={<p>new space</p>} />
        <Route path="/s/:space" element={<p>space</p>} />
        <Route path="/s/:space/new" element={<p>new page</p>} />
        <Route path="/s/:space/p/*" element={<p>page</p>} />
        <Route path="/s/:space/e/*" element={<p>editor</p>} />
      </Routes>
    </BrowserRouter>
  );
}
```

`app.css` — mobile-first baseline (system font stack, max-width 44rem centered, 16px+ tap targets, `prefers-color-scheme` dark variant; ~60 lines, written for real in this task — Task 8 loads the frontend-design skill before styling further).

- [ ] **Step 1: Scaffold and install** — write all files, `cd frontend && bun install`.
- [ ] **Step 2: Run the test** — `bun test` → summary tests pass.
- [ ] **Step 3: Build** — `bun run build` → `dist/index.html` + hashed assets exist.
- [ ] **Step 4: Wire to backend** — from repo root: `docker compose up -d`, then `RIF_DEV_INSECURE=1 RIF_DEV_PRINCIPAL_EMAIL=<seeded email> PORT=8000 uv run python -m rif.server` and `cd frontend && bun run dev`; open http://localhost:3000, confirm the router shell renders and `/api/index` returns JSON in the network tab. (If no local person row exists, create one via the existing test/Graph pattern or a one-off `piccolo` shell insert; document what you did in the commit message.)
- [ ] **Step 5: Commit** — `git add frontend && git commit -m "feat: frontend scaffold — Bun+React shell, API client, dev proxy"`

---

### Task 8: Branding — reef mark, favicon, header

rif → reef. A simple SaaS-style mark: a rounded-square tile, deep-water gradient, three white coral branches rising from a seabed arc.

**Files:**
- Create: `frontend/public/reef.svg` (Bun's html build copies referenced assets; reference it from `index.html`)
- Modify: `frontend/index.html` (favicon link + title "rif"), `frontend/src/App.tsx` (header bar), `frontend/src/app.css` (header styles)

**Process note:** load the `frontend-design:frontend-design` skill before executing this task and Tasks 9–10 — it governs the visual choices; the SVG below is the starting point, refine within its guidance.

`reef.svg` starting point:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="rif">
  <defs>
    <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0e7490"/>
      <stop offset="1" stop-color="#164e63"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="14" fill="url(#sea)"/>
  <path d="M12 46c6-3 14-3 20 0s14 3 20 0" fill="none" stroke="#67e8f9" stroke-width="3" stroke-linecap="round"/>
  <g stroke="#ffffff" stroke-width="3.5" stroke-linecap="round" fill="none">
    <path d="M22 44V30c0-4-3-5-3-9"/>
    <path d="M32 44V24c0-3 3-4 3-8"/>
    <path d="M42 44V32c0-4 3-5 3-10"/>
  </g>
</svg>
```

Header: icon + lowercase wordmark "rif" (weight 600, letter-spacing slight), links back to `/app`.

- [ ] **Step 1: Load frontend-design skill; refine and add the SVG + favicon + header.**
- [ ] **Step 2: Verify** — `bun run build`; open the dev server; icon renders crisply at 16px (favicon) and 40px (header); check both color schemes.
- [ ] **Step 3: Commit** — `git commit -m "feat: reef branding — mark, favicon, header"`

---### Task 9: Views — Home and Space

**Files:**
- Create: `frontend/src/views/Home.tsx`, `frontend/src/views/SpaceView.tsx`, `frontend/src/views/NewSpace.tsx`
- Modify: `frontend/src/App.tsx` (replace the three placeholder routes)

**Interfaces:**
- Consumes: `apiGet`, `apiSend`, `ApiError`, types from Task 7.
- Produces: route components; `SpaceView` links pages to `/s/:space/p/<path>` and the editor to `/s/:space/e/<path>`.

Behavior (write complete components; state via `useState`/`useEffect`, no data library):

- **Home**: `apiGet<{spaces: SpaceIndex[]}>("/api/index")`; render each space as a tappable card — alias, page count, "personal" first (API already orders it) — plus a "New space" link. Errors render an inline `<div class="notice">` with the message, not a toast.
- **NewSpace**: one slug input (pattern hint: lowercase, hyphens), submit → `apiSend("POST", "/api/spaces", {slug})` → navigate to `/s/<slug>`; `ApiError` 400 shows `detail` inline (e.g. name taken).
- **SpaceView**: parallel `apiGet` of `/api/index` (for this space's page list — filter by alias) and `/api/spaces/:space/members` (skip for `personal`). Pages listed with title, description, relative updated time. If `is_owner`: members panel — member list with per-member "remove" (confirm dialog via inline two-step button, **not** `window.confirm` — browser dialogs block automation and are ugly), invite form (email + optional display name) that shows the returned `disclosure` string prominently after success. "New page" link to `/s/:space/new`.

- [ ] **Step 1: Implement the three components and route them.**
- [ ] **Step 2: Verify in the dev server** — browse home → space, create a space, invite an email, remove it; confirm each API call in the network tab and each error path (create a duplicate slug) renders inline.
- [ ] **Step 3: `bun run build` passes. Commit** — `git commit -m "feat: home and space views with owner admin"`

---

### Task 10: Views — Page render, Editor, New page

**Files:**
- Create: `frontend/src/markdown.ts`, `frontend/src/views/PageView.tsx`, `frontend/src/views/Editor.tsx`, `frontend/src/views/NewPage.tsx`
- Modify: `frontend/src/App.tsx` (replace remaining placeholders)

**Interfaces:**
- Consumes: Task 7 exports; `indexDescription` from `summary.ts`.
- Produces: `renderMarkdown(body: string, space: string): string` in `markdown.ts` — markdown-it (`html: false, linkify: true`) → rewrite relative image srcs to `/api/images/<space>/<src>` (markdown-it image rule override) → `DOMPurify.sanitize`.

Behavior:

- **PageView**: load `/api/pages/:space/:path`; render `renderMarkdown` output via `dangerouslySetInnerHTML` (safe: sanitized); title from page; "Edit" link — hidden when `path` starts with `meta/` (render a "protected page" note instead).
- **Editor**: load the page; fields: title input, tags input (comma-separated), body `<textarea>` (auto-growing, `font-family: monospace`), optional "why" message input; a Preview toggle swapping textarea ↔ rendered markdown (mobile-first: toggle, not split pane). Below the body, live: `Index description: <output of indexDescription(body)>` with hint text "the first prose line becomes this page's one-line description in the index". Save → `apiSend("PUT", …, {body, title, tags, message, expected_version: version})`. On success navigate to the page view. On `ApiError` 409: keep the draft untouched, fetch the latest page, show a conflict banner — "Someone saved this page while you were editing (now v<latest.version>). Your text is kept below; the latest version is shown for comparison." — render the latest body read-only under the banner, and set `expected_version` to the latest version so the next save applies the user's (manually merged) draft.
- **NewPage**: path input (validated `^[a-z0-9-/._]+\.md$`, no `meta/` prefix — hint why), then the same editor component in create mode (`expected_version: null`).

- [ ] **Step 1: Implement `markdown.ts` + the three views; route them.**
- [ ] **Step 2: Verify in the dev server** — read a page with an image, edit+save, force a 409 (save from a second tab first), create a page; confirm the index-description preview updates live and XSS is neutralized (`<script>alert(1)</script>` in a body renders inert).
- [ ] **Step 3: `bun run build` + `bun test` pass. Commit** — `git commit -m "feat: page view, markdown editor with conflict UX, new page"`

---

### Task 11: Docker build stage + docs + operator steps

**Files:**
- Modify: `Dockerfile`, `README.md` (surfaces paragraph), `docs/runbook.md` (Open items + a new "Web frontend" section)

**Dockerfile:** add before the existing `FROM python:3.13-slim`:

```dockerfile
FROM oven/bun:1 AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build
```

and in the python stage, after `COPY src ./src`:

```dockerfile
COPY --from=frontend /fe/dist ./frontend/dist
```

(`Settings.static_dir` default `frontend/dist` resolves relative to WORKDIR `/app` — correct in the container and in local repo-root runs.)

**Runbook additions** (a short section, matching the doc's voice): new env vars `WORKOS_CLIENT_ID`, `RIF_SESSION_SECRET` (generate: `python -c "import secrets; print(secrets.token_hex(32))"`); the operator step — WorkOS dashboard → the AuthKit application → add redirect URI `{RIF_BASE_URL}/api/auth/callback`; note that until both are set, `/api/auth/login` returns 503 and the MCP surface is unaffected.

- [ ] **Step 1: Edit the three files.**
- [ ] **Step 2: Verify the image builds** — `docker build -t rif-web-test .` completes; `docker run --rm rif-web-test ls frontend/dist` shows `index.html`.
- [ ] **Step 3: Commit** — `git commit -m "feat: ship the frontend in the image; document web env and operator steps"`

---

### Task 12: Verification and security review

- [ ] **Step 1: Full backend suite** — `uv run pytest` all green; `uv run ruff check src tests` clean.
- [ ] **Step 2: Frontend** — `cd frontend && bun test && bun run build` clean.
- [ ] **Step 3: End-to-end manual pass with Playwright MCP** against the dev setup (Task 7 Step 4 recipe): login-less dev mode → home → space → page → edit → save → 409 path → invite flow → new space → new page. Screenshot each screen at mobile width (390px).
- [ ] **Step 4: Security review** — dispatch the `paranoid-security-auditor` agent on the diff (auth flow, session sealing, CSRF, static traversal, image redirect authz, markdown sanitization). Fix what it finds; re-run suites.
- [ ] **Step 5: Final commit / PR** — use superpowers:finishing-a-development-branch.
