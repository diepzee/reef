"""Routes for a signed-in test account: reset it, and ask whether it is ready.

These are plain web routes, never MCP tools, so an assistant can never call
them. The command-line tool calls them with the account's own bearer token,
the same one it uses for MCP. Each route accepts only a person flagged in
``test_accounts``; everyone else is refused, and no body names an email, a
person id or any engram text.
"""

from dataclasses import asdict
from importlib.metadata import version

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from reef.access import AccessDenied, Principal, arm
from reef.auth import principal_from_claims
from reef.config import get_settings
from reef.db import transaction_scope
from reef.identity import person_by_email
from reef.models import TestAccount
from reef.testaccount import (
    NotATestAccount,
    SharedWithOthers,
    configured_store,
    readiness,
    reset,
)

# Servers this module has registered routes on, so repeated calls do not
# append duplicate Starlette routes.
_registered: set[int] = set()


async def principal_from_bearer(request: Request) -> Principal:
    """Resolve the caller from the bearer token the serving server verified.

    FastMCP's HTTP app sends every request, these routes included, through
    its provider's authentication middleware. When the provider accepts the
    ``Authorization: Bearer`` token, the middleware leaves an
    :class:`~mcp.server.auth.middleware.bearer_auth.AuthenticatedUser` holding
    the verified token in ``request.scope["user"]``. That is the only
    verification trusted here.

    The provider is never looked up through ``reef.server``. Production runs
    ``python -m reef.server``, so the serving server is ``__main__.mcp``;
    importing ``reef.server`` would build a second server, with a provider
    that has never served a request and accepts none of the real one's
    tokens.

    When the serving server has no provider, ``REEF_DEV_INSECURE=1`` falls
    back to ``REEF_DEV_PRINCIPAL_EMAIL``, as
    :func:`reef.auth.current_principal` does.

    :param request: the incoming request
    :raises AccessDenied: when no identity can be established
    :returns: the authenticated principal
    """
    if request.app.state.fastmcp_server.auth is None:
        return await _dev_principal()
    user = request.scope.get("user")
    if not isinstance(user, AuthenticatedUser):
        raise AccessDenied("no bearer token the server accepted")
    async with transaction_scope():
        return await principal_from_claims(dict(user.access_token.claims or {}))


async def _dev_principal() -> Principal:
    """Resolve the dev person, for a server running without a provider.

    :raises AccessDenied: unless ``REEF_DEV_INSECURE=1`` names a known
        ``REEF_DEV_PRINCIPAL_EMAIL``
    :returns: the dev principal
    """
    settings = get_settings()
    email = settings.dev.principal_email or None
    if not settings.dev.insecure or not email:
        raise AccessDenied("no auth provider on this server")
    async with transaction_scope():
        identity = await person_by_email(email)
        if identity is None:
            raise AccessDenied("unknown principal")
        principal = Principal(person_id=identity.person_id, email=identity.email)
        await arm(principal)
    return principal


async def _is_flagged(principal: Principal) -> bool:
    """Say whether the caller's own ``test_accounts`` row exists.

    :param principal: the signed-in person
    :returns: True when the person is flagged as a test account
    """
    async with transaction_scope():
        await arm(principal)
        return await TestAccount.exists().where(
            TestAccount.person_id == principal.person_id
        )


def _refusal(error: str, status: int) -> Response:
    """Build a refusal that carries only its code.

    :param error: the error code
    :param status: the HTTP status
    :returns: the response
    """
    return JSONResponse({"error": error}, status_code=status)


async def _reset(request: Request) -> Response:
    """Reset the caller's test account to the made-up person.

    :param request: the incoming request
    :returns: the result as JSON, or a refusal
    """
    try:
        principal = await principal_from_bearer(request)
    except AccessDenied:
        return _refusal("unauthenticated", 401)
    try:
        result = await reset(principal, configured_store())
    except NotATestAccount:
        return _refusal("not_a_test_account", 403)
    except SharedWithOthers:
        return _refusal("shared_with_others", 403)
    return JSONResponse({**asdict(result), "reef_version": version("reef")})


async def _ready(request: Request) -> Response:
    """Report whether the caller's test account has finished its background work.

    :param request: the incoming request
    :returns: the counts as JSON, or a refusal
    """
    try:
        principal = await principal_from_bearer(request)
    except AccessDenied:
        return _refusal("unauthenticated", 401)
    if not await _is_flagged(principal):
        return _refusal("not_a_test_account", 403)
    return JSONResponse(asdict(await readiness(principal)))


def register_test_account_routes(mcp) -> None:
    """Register the ``/api/test-account/*`` routes on ``mcp``.

    Idempotent: repeated calls do not append duplicate routes.

    :param mcp: the FastMCP server to register the routes on
    """
    if id(mcp) in _registered:
        return
    _registered.add(id(mcp))
    mcp.custom_route("/api/test-account/reset", methods=["POST"])(_reset)
    mcp.custom_route("/api/test-account/ready", methods=["GET"])(_ready)
