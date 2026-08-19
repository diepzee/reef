"""Request-level auth for the web surface: cookie to principal, CSRF."""

from starlette.requests import Request
from starlette.responses import Response

from reef.access import Principal
from reef.config import env, get_settings
from reef.web.session import SESSION_TTL_SECONDS, SessionData, seal, unseal

SESSION_COOKIE = "rif_session"
CSRF_HEADER = "x-rif-csrf"
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


class Unauthenticated(Exception):
    """No valid session on the request."""


class CsrfRejected(Exception):
    """A mutating request without the CSRF header."""


def session_from_request(request: Request) -> SessionData | None:
    """Return the verified session on this request, if there is one.

    :param request: the incoming request
    :returns: the session data, or None when absent or invalid
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return unseal(token, secret=get_settings().session_secret)


def principal_from_request(request: Request) -> Principal:
    """Resolve the principal from the session cookie.

    Dev fallback: with ``REEF_DEV_INSECURE=1`` and ``REEF_DEV_PRINCIPAL_EMAIL``
    set, an anonymous request resolves to the dev person — mirroring the
    stdio fallback in ``reef.auth`` and equally dead in production, where
    neither variable is set.

    :param request: the incoming request
    :raises Unauthenticated: when no valid session exists
    :returns: the authenticated principal
    """
    data = session_from_request(request)
    if data is not None:
        return Principal(person_id=data.person_id, email=data.email)
    if env("DEV_INSECURE") == "1":
        email = env("DEV_PRINCIPAL_EMAIL")
        if email:
            # Local dev only: person lookup happens in the handler's
            # transaction, in the api() wrapper in routes_api.py.
            raise _DevFallback(email)
    raise Unauthenticated


class _DevFallback(Exception):
    """Internal: carries the dev email to the handler wrapper."""

    def __init__(self, email: str) -> None:
        """Initialize with a dev email."""
        self.email = email


def require_csrf(request: Request) -> None:
    """Reject mutating requests that lack the CSRF header.

    :param request: the incoming request
    :raises CsrfRejected: for a mutation without ``X-Rif-Csrf: 1``
    """
    if request.method in _MUTATING and request.headers.get(CSRF_HEADER) != "1":
        raise CsrfRejected


def cookie_secure() -> bool:
    """Return whether session-family cookies should be marked ``Secure``.

    Railway terminates TLS at a proxy in front of the app; uvicorn's
    proxy-header trust is not configured, so every request the app sees
    arrives as plain ``http`` regardless of what the browser used. That
    makes ``request.url.scheme`` unreliable as a security signal -- deriving
    ``Secure`` from it means the flag silently never fires in production.
    Instead, ``Secure`` is tied to the same escape hatch that already gates
    the plaintext-HTTP boot guard in ``reef.server.main``: on by default, and
    off only when ``REEF_DEV_INSECURE=1`` deliberately opts into local
    development over plain HTTP.

    :returns: True unless ``REEF_DEV_INSECURE=1`` is set
    """
    return env("DEV_INSECURE") != "1"


def session_sid(request: Request) -> str | None:
    """Read the upstream AuthKit session id off the request's session cookie.

    :param request: the incoming request
    :returns: the sid the session was sealed with, or None
    """
    data = session_from_request(request)
    return data.sid if data else None


def set_session_cookie(
    response: Response,
    principal: Principal,
    *,
    secure: bool,
    sid: str | None = None,
    issued_at: float | None = None,
    epoch: int = 0,
) -> None:
    """Seal a fresh 7-day session onto the response — the sliding renewal.

    ``issued_at`` is the load-bearing argument. Renewing a session means
    sealing a new token, and a new token's ``iat`` defaults to now — so a
    renewal that does not pass the previous value through restarts the
    absolute ceiling on every request, and the ceiling never arrives. Callers
    renewing an existing session must pass the value they read off the
    incoming cookie; only a genuinely new sign-in leaves it None.

    :param response: the response to set the cookie on
    :param principal: the authenticated person
    :param secure: whether to mark the cookie Secure (https requests)
    :param sid: the upstream AuthKit session id to carry, when known
    :param issued_at: when this renewal chain began; None starts a new one
    :param epoch: the person's current ``session_epoch``
    """
    token = seal(
        principal.person_id,
        principal.email,
        secret=get_settings().session_secret,
        sid=sid,
        issued_at=issued_at,
        epoch=epoch,
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
