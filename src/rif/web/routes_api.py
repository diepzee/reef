"""The JSON API: reads (``/api/me``, ``/api/index``, page and image fetch),
writes (page save), and space administration (create, invite, members,
removal).

Every route goes through :func:`api`, which opens the request's single
transaction, resolves the principal (including the dev fallback), enforces
CSRF on mutations, maps domain exceptions onto the Global Constraints error
table, and renews the session cookie on success.
"""

import json
from collections.abc import Callable
from dataclasses import asdict

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from rif.access import AccessDenied, Principal, resolve_space
from rif.attachments import S3ObjectStore, get_attachment
from rif.config import get_settings
from rif.context import build_index, latest_editors
from rif.db import transaction_scope
from rif.models import Page, Person
from rif.pages import ProtectedPath, VersionConflict, get_page, save_page
from rif.spaces import SpaceError, create_space, invite, member_roster, remove_member
from rif.web.requests import (
    CsrfRejected,
    Unauthenticated,
    _DevFallback,
    cookie_secure,
    principal_from_request,
    require_csrf,
    session_sid,
    set_session_cookie,
)

# Servers this module has registered routes on, so repeated calls (every
# test that wants a fresh app) don't append duplicate Starlette routes --
# mirrors the pattern in rif.web.routes_auth.
_registered: set[int] = set()


class BadRequest(Exception):
    """Raised when a request's JSON body is malformed, absent, or mistyped.

    Every write handler raises this instead of reaching into the domain
    layer with untrusted shapes: :func:`api` maps it to a clean 400
    ``{"error": "bad_request"}`` so a bare int, a JSON syntax error, or a
    wrong-typed field never reaches ``save_page`` or the database driver.
    """


async def _json_body(request: Request) -> dict:
    """Parse a request's JSON body and require it to be an object.

    :param request: the incoming request
    :raises BadRequest: if the body is not valid JSON, or parses to
        anything other than a JSON object (a bare int, list, or string)
    :returns: the parsed body
    """
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise BadRequest("request body is not valid JSON") from None
    if not isinstance(payload, dict):
        raise BadRequest("request body must be a JSON object")
    return payload


def _require_str(payload: dict, key: str) -> str:
    """Fetch a required string field from a parsed JSON body.

    :param payload: the parsed request body
    :param key: the field name
    :raises BadRequest: if the key is absent or not a string
    :returns: the field's value
    """
    if key not in payload or not isinstance(payload[key], str):
        raise BadRequest(f"{key!r} is required and must be a string")
    return payload[key]


def _optional_str(payload: dict, key: str) -> str | None:
    """Fetch an optional, nullable string field from a parsed JSON body.

    :param payload: the parsed request body
    :param key: the field name
    :raises BadRequest: if present and neither a string nor null
    :returns: the field's value, or None if absent or null
    """
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise BadRequest(f"{key!r} must be a string or null")
    return value


def _optional_tags(payload: dict) -> list[str] | None:
    """Fetch the optional, nullable ``tags`` field from a page-save body.

    :param payload: the parsed request body
    :raises BadRequest: if present and not a list of strings
    :returns: the tag list, or None if absent or null
    """
    tags = payload.get("tags")
    if tags is not None and (
        not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags)
    ):
        raise BadRequest("'tags' must be a list of strings or null")
    return tags


def _optional_int(payload: dict, key: str) -> int | None:
    """Fetch an optional, nullable integer field from a parsed JSON body.

    Booleans are rejected even though ``bool`` is a subtype of ``int`` in
    Python -- a request body that means to send a version number never
    means ``true``/``false``.

    :param payload: the parsed request body
    :param key: the field name
    :raises BadRequest: if present and not an integer
    :returns: the field's value, or None if absent or null
    """
    value = payload.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise BadRequest(f"{key!r} must be an integer or null")
    return value


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
                else:
                    # A validly-signed cookie can outlive the person it names
                    # (deleted since sealing) -- confirm the row still exists
                    # rather than let a phantom principal reach the handler.
                    person = (
                        await Person.objects()
                        .where(Person.id == principal.person_id)
                        .first()
                    )
                    if person is None:
                        raise Unauthenticated from None
                result = await handler(request, principal)
        except Unauthenticated:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        except BadRequest:
            return JSONResponse({"error": "bad_request"}, status_code=400)
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
            response, principal, secure=cookie_secure(), sid=session_sid(request)
        )
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


async def _page_payload(space: str, page: Page) -> dict:
    """Shape a page row into the JSON API's page representation.

    Shared by the GET and PUT handlers so both carry the same fields,
    including the newest revision's author -- a one-page batch through
    :func:`latest_editors`, resolved fresh on every request rather than
    denormalized onto the page row.

    :param space: the space alias the page was fetched through
    :param page: the saved or fetched page row
    :returns: the page, shaped for the API response
    """
    editors = await latest_editors([page.id])
    return {
        "space": space,
        "path": page.path,
        "title": page.title,
        "tags": list(page.tags),
        "body": page.body,
        "version": page.version,
        "updated": page.updated_at.isoformat(),
        "last_editor": editors.get(page.id),
    }


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
    return await _page_payload(space, page)


async def _put_page(request: Request, principal: Principal) -> dict:
    """Create or overwrite a page from a JSON body.

    ``expected_version`` is optional: omitted or ``null`` means create or
    overwrite without an optimistic-lock check; an int enforces one, raising
    ``VersionConflict`` (mapped to 409 by :func:`api`) on a stale value.

    :param request: the incoming request, carrying ``space`` and ``path``
        path params and a JSON body with ``body`` and ``message`` required,
        and optional ``title``, ``tags``, ``expected_version``
    :param principal: the authenticated person
    :raises BadRequest: for malformed JSON, a non-object body, a missing
        ``body``/``message``, or any field of the wrong type
    :returns: the saved page, shaped as in Task 4's GET
    """
    space = request.path_params["space"]
    path = request.path_params["path"]
    payload = await _json_body(request)
    body = _require_str(payload, "body")
    message = _require_str(payload, "message")
    title = _optional_str(payload, "title")
    tags = _optional_tags(payload)
    expected_version = _optional_int(payload, "expected_version")
    page = await save_page(
        principal,
        space,
        path,
        body,
        message=message,
        title=title,
        tags=tags,
        expected_version=expected_version,
    )
    return await _page_payload(space, page)


async def _create_space(request: Request, principal: Principal) -> dict:
    """Create a shared space owned by the caller.

    :param request: the incoming request, carrying a JSON body with ``slug``
        required
    :param principal: the authenticated person
    :raises BadRequest: for malformed JSON, a non-object body, a missing
        ``slug``, or a non-string ``slug``
    :returns: the new space's alias and slug
    """
    payload = await _json_body(request)
    slug = _require_str(payload, "slug")
    space = await create_space(principal, slug)
    return {"alias": space.slug, "slug": space.slug}


async def _space_members(request: Request, principal: Principal) -> dict:
    """List a shared space's members and ownership.

    Email addresses go out only to the owner. The members panel that
    consumes this is owner-only in the frontend, and a non-owner member has
    no legitimate need to see other members' addresses, so a non-owner's
    roster keeps the same shape with each ``email`` blanked to ``""``
    rather than the field dropped.

    :param request: the incoming request, carrying a ``space`` path param
    :param principal: the authenticated person
    :returns: member display name/email pairs (email blank for
        non-owners), the owner's email, and whether the caller is the owner
    """
    slug = request.path_params["space"]
    space = await resolve_space(principal, slug)
    owner = await Person.objects().where(Person.id == space.owner_person_id).first()
    is_owner = space.owner_person_id == principal.person_id
    roster = await member_roster(space.id)
    if not is_owner:
        roster = [
            {"display_name": member["display_name"], "email": ""} for member in roster
        ]
    return {
        "members": roster,
        "owner_email": owner.email if owner else "",
        "is_owner": is_owner,
    }


async def _invite(request: Request, principal: Principal) -> dict:
    """Invite an email address into a shared space the caller owns.

    :param request: the incoming request, carrying a ``space`` path param
        and a JSON body with ``email`` required and ``display_name`` optional
    :param principal: the authenticated person
    :raises BadRequest: for malformed JSON, a non-object body, a missing
        ``email``, or any field of the wrong type
    :returns: the invite outcome, including the disclosure text the UI must
        show
    """
    slug = request.path_params["space"]
    payload = await _json_body(request)
    email = _require_str(payload, "email")
    display_name = _optional_str(payload, "display_name")
    return await invite(principal, slug, email, display_name)


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
