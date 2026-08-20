"""Browser OIDC login: the authorization-code + PKCE dance against AuthKit.

Four plain Starlette routes, registered onto the FastMCP server via
``custom_route``: ``GET /api/auth/login`` starts the redirect, ``GET
/api/auth/callback`` binds the returning code to a principal and opens a
session, ``POST /api/auth/join`` admits a stranger while the launch door is
open (:mod:`reef.opendoor`), and ``POST /api/auth/logout`` clears it.
"""

import hmac
import os
import secrets
from collections.abc import Callable
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from reef import audit
from reef.access import AccessDenied, Principal, arm
from reef.auth import principal_from_claims
from reef.config import env, get_settings
from reef.coves import ensure_personal_cove
from reef.db import transaction_scope
from reef.identity import person_session_epoch, revoke_sessions
from reef.opendoor import admit, door_policy
from reef.web.oidc import (
    OAUTH_COOKIE_TTL_SECONDS,
    AuthKitOIDC,
    OIDCClient,
    OIDCError,
    _seal_oauth,
    _unseal_oauth,
    authkit_domain,
    pkce_pair,
    seal_join,
    token_sid,
    unseal_join,
)
from reef.web.requests import (
    SESSION_COOKIE,
    CsrfRejected,
    cookie_secure,
    require_csrf,
    session_from_request,
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

#: Carries the verified claims across the click on the open door. Separate
#: from OAUTH_COOKIE because it outlives the callback that set it.
OPEN_DOOR_COOKIE = "rif_join"

#: Placeholder in ``site/invite-only.html`` where the rejected address goes.
_EMAIL_SLOT = "__EMAIL__"

#: Shown if the page is missing from the image, so a deployment packaging
#: mistake still explains itself rather than serving a blank 403.
_DENIED_FALLBACK = (
    "This site is invite-only. You get in when someone already using it "
    "invites the address you signed in with."
)


def _open_door_page(email: str) -> str:
    """Render the launch page, naming the address that will be admitted.

    Falls back to the plain refusal if the page is missing from the image:
    a packaging mistake should shut the door, never open it wider than the
    operator asked for.

    :param email: the verified address the button will admit
    :returns: the page's HTML
    """
    path = Path(get_settings().site_dir) / "open-door.html"
    try:
        template = path.read_text(encoding="utf-8")
    except OSError:
        return _DENIED_FALLBACK
    return template.replace(_EMAIL_SLOT, escape(email))


def _wall(claims: dict) -> Response:
    """Return what an uninvited but verified visitor should see.

    One place decides, so the closed-door behaviour and the launch behaviour
    cannot drift: with the door shut this is byte-for-byte what every
    deployment has served until now.

    :param claims: the verified OIDC claims
    :returns: the 403 refusal, or a 200 page carrying the sealed claims
    """
    email = claims.get("email")
    subject = claims.get("sub")
    policy = door_policy()
    if not policy.is_open or not email or not subject:
        return HTMLResponse(_denied_page(email), status_code=403)
    response = HTMLResponse(_open_door_page(email))
    response.set_cookie(
        OPEN_DOOR_COOKIE,
        seal_join(
            email,
            subject,
            claims.get("name") or email.split("@")[0],
            secret=get_settings().session_secret,
        ),
        max_age=OAUTH_COOKIE_TTL_SECONDS,
        httponly=True,
        # Lax is doing real work, not ceremony: it is what stops a
        # cross-site POST from carrying this cookie to the join route. The
        # CSRF header is the other half, and neither is sufficient alone --
        # the header because a plain form cannot set one, this because a
        # top-level navigation still sends it.
        samesite="lax",
        secure=cookie_secure(),
    )
    return response


def _denied_page(email: str | None) -> str:
    """Render the invite-only page, naming the address that was refused.

    Showing the address matters: people routinely hold several Google
    accounts and pick the wrong one, and "not invited" is unhelpful if you
    cannot see *which* identity was refused.

    The value arrives in verified OIDC claims rather than raw user input,
    but it is escaped regardless — reflecting an identity string into a page
    is not somewhere to reason about whether escaping is needed.

    :param email: the address that was refused, if the claims carried one
    :returns: the page's HTML
    """
    shown = escape(email) if email else "an address this site doesn't recognise"
    path = Path(get_settings().site_dir) / "invite-only.html"
    try:
        template = path.read_text(encoding="utf-8")
    except OSError:
        return _DENIED_FALLBACK
    return template.replace(_EMAIL_SLOT, shown)


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
        base_url = env("BASE_URL") or ""
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
        redirect_uri = f"{(env('BASE_URL') or '')}/api/auth/callback"
        try:
            access_token = await client.exchange(code, verifier, redirect_uri)
            claims = await client.userinfo(access_token)
        except OIDCError:
            return JSONResponse({"error": "oidc_upstream"}, status_code=502)

        try:
            async with transaction_scope():
                principal = await principal_from_claims(claims)
                # Sealed into the cookie and compared on every later request,
                # so a bump ends this session. Read here rather than assumed
                # zero: a person who has revoked before is past zero, and a
                # cookie carrying the wrong epoch would be dead on arrival.
                epoch = await person_session_epoch(principal.person_id) or 0
        except AccessDenied:
            # principal_from_claims is deliberately untouched by the launch
            # exception: its rule stays "unknown subject, no allowlist row,
            # denied". Admission is a separate and explicit act, so a
            # misconfigured flag can never make this callback quietly mint
            # accounts -- the most it can do is offer a button.
            return _wall(claims)

        response = RedirectResponse("/app", status_code=303)
        # A genuinely new session, so issued_at is left to default to now:
        # this is the one place that starts a renewal chain rather than
        # continuing one.
        set_session_cookie(
            response,
            principal,
            secure=cookie_secure(),
            sid=token_sid(access_token),
            epoch=epoch,
        )
        response.delete_cookie(OAUTH_COOKIE)
        return response

    async def logout(request: Request) -> Response:
        """End every session this person holds, and hand back the upstream URL.

        Deleting the cookie is not logging out. The cookie is a signed
        bearer token: a copy taken beforehand keeps working, renewing itself
        on every request, and the person who pressed the button has no way
        to stop it. So logout moves the person's ``session_epoch`` on, which
        invalidates every token sealed before this moment.

        That makes logout global rather than per-device, which is a real
        cost -- signing out on a laptop signs out the phone. It is the right
        trade here: the thing being protected is the whole of somebody's
        private memory, the cost of being too aggressive is signing in
        again, and the cost of being too weak is silent, permanent access by
        whoever holds a copy. Anyone wanting per-device logout needs a stored
        session per device, which is a different design, not a smaller one.

        Revocation is deliberately best-effort about the rest: if the epoch
        bump fails the cookie is still cleared and the upstream URL still
        returned, because a logout that reports failure teaches people to
        ignore it.

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
        session = session_from_request(request)
        if session is not None:
            async with transaction_scope():
                principal = Principal(person_id=session.person_id, email=session.email)
                await arm(principal)
                await revoke_sessions(session.person_id)
        sid = session_sid(request)
        if sid:
            query = urlencode(
                {
                    "session_id": sid,
                    "return_to": (f"{(env('BASE_URL') or '')}/app/signed-out"),
                }
            )
            body["logout_url"] = f"{WORKOS_LOGOUT_URL}?{query}"
        response = JSONResponse(body)
        response.delete_cookie(SESSION_COOKIE)
        return response

    async def join(request: Request) -> Response:
        """Admit a verified stranger through the open door, if it is open.

        The launch exception's only entry point. Everything it needs arrives
        in the sealed cookie the wall set, because the caller has no session
        -- and everything it grants is checked here rather than there: the
        seats can run out while somebody reads the page, so the button is an
        offer, never authority.

        :param request: the incoming request; must carry the CSRF header and
            the sealed claims cookie
        :returns: ``{"ok": true}`` with a session cookie on success; 403 for
            a missing header, missing or defective claims, or a door that is
            shut or full
        """
        try:
            require_csrf(request)
        except CsrfRejected:
            return JSONResponse({"error": "csrf"}, status_code=403)
        sealed = request.cookies.get(OPEN_DOOR_COOKIE)
        unsealed = (
            unseal_join(sealed, secret=get_settings().session_secret)
            if sealed
            else None
        )
        if unsealed is None:
            return JSONResponse({"error": "no_claims"}, status_code=403)
        email, subject, display_name = unsealed

        async with transaction_scope():
            identity = await admit(email, subject, display_name)
            if identity is None:
                return JSONResponse({"error": "door_closed"}, status_code=403)
            principal = Principal(person_id=identity.person_id, email=identity.email)
            # Armed before onboarding, for the reason principal_from_claims
            # spells out: ensure_personal_cove inserts a cove and a
            # membership, and those inserts are checked against the armed
            # principal.
            await arm(principal)
            await ensure_personal_cove(identity.person_id, identity.email)
            audit.record(audit.OPEN_DOOR_ADMITTED, actor=identity.person_id)
            epoch = await person_session_epoch(identity.person_id) or 0

        response = JSONResponse({"ok": True})
        set_session_cookie(response, principal, secure=cookie_secure(), epoch=epoch)
        # The claims have been spent. Leaving the cookie would let a second
        # POST run the whole admission again, which the unique index on
        # email would refuse -- as a 403 rather than anything worse, but a
        # spent credential should not outlive its use.
        response.delete_cookie(OPEN_DOOR_COOKIE)
        return response

    mcp.custom_route("/api/auth/login", methods=["GET"])(login)
    mcp.custom_route("/api/auth/callback", methods=["GET"])(callback)
    mcp.custom_route("/api/auth/join", methods=["POST"])(join)
    mcp.custom_route("/api/auth/logout", methods=["POST"])(logout)
