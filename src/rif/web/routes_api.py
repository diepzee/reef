"""The JSON API: reads (``/api/me``, ``/api/index``, page and image fetch),
writes (page save), and space administration (create, invite, members,
removal).

Every route goes through :func:`api`, which opens the request's single
transaction, resolves the principal (including the dev fallback), enforces
CSRF on mutations, maps domain exceptions onto the Global Constraints error
table, and renews the session cookie on success.
"""

from collections.abc import Callable
from dataclasses import asdict

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from rif.access import AccessDenied, Principal, resolve_space
from rif.attachments import S3ObjectStore, get_attachment
from rif.config import get_settings
from rif.context import build_index
from rif.db import transaction_scope
from rif.models import Person
from rif.pages import ProtectedPath, VersionConflict, get_page, save_page
from rif.spaces import SpaceError, create_space, invite, member_names, remove_member
from rif.web.requests import (
    CsrfRejected,
    Unauthenticated,
    _DevFallback,
    principal_from_request,
    require_csrf,
    set_session_cookie,
)

# Servers this module has registered routes on, so repeated calls (every
# test that wants a fresh app) don't append duplicate Starlette routes --
# mirrors the pattern in rif.web.routes_auth.
_registered: set[int] = set()


def api(handler: Callable) -> Callable:
    """Wrap a handler with transaction, auth, CSRF, errors, and renewal.

    :param handler: async ``(request, principal) -> Response | dict``
    :returns: a Starlette-compatible endpoint
    """

    async def endpoint(request: Request) -> Response:
        """Run ``handler`` inside the standard request pipeline.

        :param request: the incoming request
        :returns: the mapped response
        """
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
        set_session_cookie(response, principal, secure=request.url.scheme == "https")
        return response

    return endpoint


async def _me(request: Request, principal: Principal) -> dict:
    """Return the logged-in person's identity.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: person id, email, and display name
    """
    person = await Person.objects().where(Person.id == principal.person_id).first()
    return {
        "person_id": str(principal.person_id),
        "email": principal.email,
        "display_name": person.display_name if person else "",
    }


async def _index(request: Request, principal: Principal) -> dict:
    """Return the index of everything the principal may see.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: the index payload, as a plain dict
    """
    return asdict(await build_index(principal))


async def _get_page(request: Request, principal: Principal) -> Response | dict:
    """Fetch a single page by space and path.

    :param request: the incoming request, carrying ``space`` and ``path``
        path params
    :param principal: the authenticated person
    :returns: the page as a dict, or a 404 JSON response if absent
    """
    space = request.path_params["space"]
    path = request.path_params["path"]
    page = await get_page(principal, space, path)
    if page is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {
        "space": space,
        "path": page.path,
        "title": page.title,
        "tags": list(page.tags),
        "body": page.body,
        "version": page.version,
        "updated": page.updated_at.isoformat(),
    }


async def _put_page(request: Request, principal: Principal) -> Response | dict:
    """Create or overwrite a page from a JSON body.

    ``expected_version`` is optional: omitted or ``null`` means create or
    overwrite without an optimistic-lock check; an int enforces one, raising
    ``VersionConflict`` (mapped to 409 by :func:`api`) on a stale value.

    :param request: the incoming request, carrying ``space`` and ``path``
        path params and a JSON body with ``body`` and ``message`` required,
        and optional ``title``, ``tags``, ``expected_version``
    :param principal: the authenticated person
    :returns: the saved page, shaped as in Task 4's GET, or a 400 JSON
        response if a required field is missing
    """
    space = request.path_params["space"]
    path = request.path_params["path"]
    payload = await request.json()
    if "body" not in payload or "message" not in payload:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    page = await save_page(
        principal,
        space,
        path,
        payload["body"],
        message=payload["message"],
        title=payload.get("title"),
        tags=payload.get("tags"),
        expected_version=payload.get("expected_version"),
    )
    return {
        "space": space,
        "path": page.path,
        "title": page.title,
        "tags": list(page.tags),
        "body": page.body,
        "version": page.version,
        "updated": page.updated_at.isoformat(),
    }


async def _create_space(request: Request, principal: Principal) -> Response | dict:
    """Create a shared space owned by the caller.

    :param request: the incoming request, carrying a JSON body with ``slug``
        required
    :param principal: the authenticated person
    :returns: the new space's alias and slug, or a 400 JSON response if
        ``slug`` is missing
    """
    payload = await request.json()
    if "slug" not in payload:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    space = await create_space(principal, payload["slug"])
    return {"alias": space.slug, "slug": space.slug}


async def _space_members(request: Request, principal: Principal) -> dict:
    """List a shared space's members and ownership.

    :param request: the incoming request, carrying a ``space`` path param
    :param principal: the authenticated person
    :returns: member display names, the owner's email, and whether the
        caller is the owner
    """
    slug = request.path_params["space"]
    space = await resolve_space(principal, slug)
    owner = await Person.objects().where(Person.id == space.owner_person_id).first()
    return {
        "members": await member_names(space.id),
        "owner_email": owner.email if owner else "",
        "is_owner": space.owner_person_id == principal.person_id,
    }


async def _invite(request: Request, principal: Principal) -> Response | dict:
    """Invite an email address into a shared space the caller owns.

    :param request: the incoming request, carrying a ``space`` path param
        and a JSON body with ``email`` required and ``display_name`` optional
    :param principal: the authenticated person
    :returns: the invite outcome, including the disclosure text the UI must
        show, or a 400 JSON response if ``email`` is missing
    """
    slug = request.path_params["space"]
    payload = await request.json()
    if "email" not in payload:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    return await invite(principal, slug, payload["email"], payload.get("display_name"))


async def _remove_member(request: Request, principal: Principal) -> dict:
    """Remove a member from a shared space the caller owns.

    :param request: the incoming request, carrying ``space`` and ``email``
        path params
    :param principal: the authenticated person
    :returns: the removal outcome
    """
    slug = request.path_params["space"]
    email = request.path_params["email"]
    return await remove_member(principal, slug, email)


async def _get_image(request: Request, principal: Principal) -> Response:
    """Redirect to a signed URL for an attachment, after checking visibility.

    :param request: the incoming request, carrying ``space`` and ``key``
        path params
    :param principal: the authenticated person
    :returns: a 302 redirect to the signed URL, or 404 if not visible
    """
    space = request.path_params["space"]
    key = request.path_params["key"]
    attachment = await get_attachment(principal, space, key)
    if attachment is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    settings = get_settings()
    url = await S3ObjectStore().signed_url(key, settings.signed_url_ttl_seconds)
    return RedirectResponse(url, status_code=302)


def register_api_routes(mcp) -> None:
    """Register the ``/api/*`` data endpoints on ``mcp``.

    Idempotent: repeated calls (as tests make, each wanting a fresh route
    registration against the shared server) do not append duplicate routes.

    :param mcp: the FastMCP server to register the routes on
    """
    if id(mcp) in _registered:
        return
    _registered.add(id(mcp))

    mcp.custom_route("/api/me", methods=["GET"])(api(_me))
    mcp.custom_route("/api/index", methods=["GET"])(api(_index))
    mcp.custom_route("/api/pages/{space}/{path:path}", methods=["GET"])(api(_get_page))
    mcp.custom_route("/api/pages/{space}/{path:path}", methods=["PUT"])(api(_put_page))
    mcp.custom_route("/api/images/{space}/{key:path}", methods=["GET"])(api(_get_image))
    mcp.custom_route("/api/spaces", methods=["POST"])(api(_create_space))
    mcp.custom_route("/api/spaces/{space}/members", methods=["GET"])(
        api(_space_members)
    )
    mcp.custom_route("/api/spaces/{space}/invites", methods=["POST"])(api(_invite))
    mcp.custom_route("/api/spaces/{space}/members/{email}", methods=["DELETE"])(
        api(_remove_member)
    )
