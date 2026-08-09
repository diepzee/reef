"""Browser OIDC login: the authorization-code + PKCE dance against AuthKit.

Three plain Starlette routes, registered onto the FastMCP server via
``custom_route``: ``GET /api/auth/login`` starts the redirect, ``GET
/api/auth/callback`` binds the returning code to a principal and opens a
session, and ``POST /api/auth/logout`` clears it.
"""

import hmac
import os
import secrets
from collections.abc import Callable
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from rif.access import AccessDenied
from rif.auth import principal_from_claims
from rif.config import get_settings
from rif.db import transaction_scope
from rif.web.oidc import (
    OAUTH_COOKIE_TTL_SECONDS,
    AuthKitOIDC,
    OIDCClient,
    OIDCError,
    _seal_oauth,
    _unseal_oauth,
    authkit_domain,
    pkce_pair,
    token_sid,
)
from rif.web.requests import (
    SESSION_COOKIE,
    CsrfRejected,
    cookie_secure,
    require_csrf,
    session_sid,
    set_session_cookie,
)

# WorkOS's session-logout endpoint: ends the AuthKit session named by the
# sid claim, then redirects the browser to ``return_to``. Ending only rif's
# own cookie is not enough — the SPA bounces any unauthenticated visit
# straight back into /api/auth/login, where a live AuthKit session silently
# re-issues a code and signs the user right back in.
WORKOS_LOGOUT_URL = "https://api.workos.com/user_management/sessions/logout"

OAUTH_COOKIE = "rif_oauth"

_DENIED_HTML = (
    "This email isn't a member of any rif space. "
    "Ask the person who runs your space to invite you."
)

# Servers this module has registered routes on, so repeated calls (every
# test that wants its own fake OIDC client) don't append duplicate
# Starlette routes -- FastMCP has no route-replacement API, and a duplicate
# route would just shadow the later registration's client_factory forever.
_registered: set[int] = set()
# The client factory each registered server should use right now. Kept
# separate from `_registered` so a later call can swap the factory (as
# tests do) even though the routes themselves are only appended once.
_factories: dict[int, Callable[[], OIDCClient]] = {}


def register_auth_routes(
    mcp, client_factory: Callable[[], OIDCClient] = AuthKitOIDC
) -> None:
    """Register the login, callback, and logout routes on ``mcp``.

    :param mcp: the FastMCP server to register the routes on
    :param client_factory: builds the :class:`OIDCClient` each request
        uses; defaults to the real AuthKit client
    """
    _factories[id(mcp)] = client_factory
    if id(mcp) in _registered:
        return
    _registered.add(id(mcp))

    async def login(request: Request) -> Response:
        """Redirect to AuthKit with a fresh PKCE challenge and CSRF state.

        :param request: the incoming request
        :returns: a 302 redirect, or 503 if auth is unconfigured
        """
        domain = authkit_domain()
        client_id = os.environ.get("WORKOS_CLIENT_ID", "")
        base_url = os.environ.get("RIF_BASE_URL", "")
        if not domain or not client_id or not base_url:
            return JSONResponse({"error": "auth_unconfigured"}, status_code=503)
        state = secrets.token_hex(16)
        verifier, challenge = pkce_pair()
        redirect_uri = f"{base_url}/api/auth/callback"
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "openid profile email",
            }
        )
        response = RedirectResponse(
            f"{domain}/oauth2/authorize?{query}", status_code=302
        )
        token = _seal_oauth(state, verifier, secret=get_settings().session_secret)
        response.set_cookie(
            OAUTH_COOKIE,
            token,
            max_age=OAUTH_COOKIE_TTL_SECONDS,
            httponly=True,
            samesite="lax",
            secure=cookie_secure(),
        )
        return response

    async def callback(request: Request) -> Response:
        """Bind the returning code to a principal and open a session.

        :param request: the incoming request, carrying ``code`` and
            ``state`` query params and the ``rif_oauth`` cookie
        :returns: 303 to ``/app`` with a session cookie on success; 400 on
            state mismatch, 403 if the email isn't invited, 502 on upstream
            failure
        """
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        oauth_token = request.cookies.get(OAUTH_COOKIE)
        unsealed = (
            _unseal_oauth(oauth_token, secret=get_settings().session_secret)
            if oauth_token
            else None
        )
        if not code or not state or unsealed is None:
            return JSONResponse({"error": "oauth_state"}, status_code=400)
        stored_state, verifier = unsealed
        if not hmac.compare_digest(stored_state, state):
            return JSONResponse({"error": "oauth_state"}, status_code=400)

        client = _factories[id(mcp)]()
        redirect_uri = f"{os.environ.get('RIF_BASE_URL', '')}/api/auth/callback"
        try:
            access_token = await client.exchange(code, verifier, redirect_uri)
            claims = await client.userinfo(access_token)
        except OIDCError:
            return JSONResponse({"error": "oidc_upstream"}, status_code=502)

        try:
            async with transaction_scope():
                principal = await principal_from_claims(claims)
        except AccessDenied:
            return HTMLResponse(_DENIED_HTML, status_code=403)

        response = RedirectResponse("/app", status_code=303)
        set_session_cookie(
            response, principal, secure=cookie_secure(), sid=token_sid(access_token)
        )
        response.delete_cookie(OAUTH_COOKIE)
        return response

    async def logout(request: Request) -> Response:
        """Clear the session cookie and hand back the upstream logout URL.

        :param request: the incoming request; must carry the CSRF header
        :returns: ``{"ok": true}`` plus, when the session carries a sid,
            a ``logout_url`` the browser must navigate to so the AuthKit
            session ends too; 403 if the CSRF header is missing
        """
        try:
            require_csrf(request)
        except CsrfRejected:
            return JSONResponse({"error": "csrf"}, status_code=403)
        body: dict = {"ok": True}
        sid = session_sid(request)
        if sid:
            query = urlencode(
                {
                    "session_id": sid,
                    "return_to": (
                        f"{os.environ.get('RIF_BASE_URL', '')}/app/signed-out"
                    ),
                }
            )
            body["logout_url"] = f"{WORKOS_LOGOUT_URL}?{query}"
        response = JSONResponse(body)
        response.delete_cookie(SESSION_COOKIE)
        return response

    mcp.custom_route("/api/auth/login", methods=["GET"])(login)
    mcp.custom_route("/api/auth/callback", methods=["GET"])(callback)
    mcp.custom_route("/api/auth/logout", methods=["POST"])(logout)
