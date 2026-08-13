"""The JSON API: reads (``/api/me``, ``/api/index``, page and file fetch),
writes (page save), and space administration (create, invite, members,
removal).

Every route goes through :func:`api`, which opens the request's single
transaction, resolves the principal (including the dev fallback), enforces
CSRF on mutations, maps domain exceptions onto the Global Constraints error
table, and renews the session cookie on success.
"""

import base64
import binascii
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from urllib.parse import urlencode

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from rif.access import AccessDenied, Principal, arm, resolve_space
from rif.account import delete_account_rows
from rif.appearance import AppearanceError, get_appearances, set_appearance
from rif.attachments import S3ObjectStore, erase_objects, get_attachment
from rif.config import get_settings
from rif.context import build_index, latest_editors
from rif.db import transaction_scope
from rif.export import build_full_dump, build_json_export, build_markdown_archive
from rif.identity import person_by_email, person_session_epoch
from rif.invitations import (
    INVITE_BUDGET,
    INVITE_WINDOW_DAYS,
    InviteBudgetExceeded,
    invite_to_reef,
    invites_left,
)
from rif.models import Page, Person
from rif.pages import (
    InvalidPath,
    PageNotFound,
    PageTooLarge,
    PrivateContentLeak,
    ProtectedPath,
    VersionConflict,
    delete_page,
    get_page,
    save_page,
)
from rif.spaces import (
    SpaceError,
    create_space,
    delete_space,
    invite,
    leave_space,
    member_roster,
    remove_member,
    rename_cove,
    space_owner,
)
from rif.web.requests import (
    SESSION_COOKIE,
    CsrfRejected,
    Unauthenticated,
    _DevFallback,
    cookie_secure,
    principal_from_request,
    require_csrf,
    session_from_request,
    session_sid,
    set_session_cookie,
)
from rif.web.routes_auth import WORKOS_LOGOUT_URL

# Servers this module has registered routes on, so repeated calls (every
# test that wants a fresh app) don't append duplicate Starlette routes --
# mirrors the pattern in rif.web.routes_auth.
_registered: set[int] = set()
# Private response marker consumed by :func:`api`: an erased account must
# clear its session rather than receive the wrapper's usual sliding renewal.
_ACCOUNT_DELETED_HEADER = "x-rif-account-deleted"


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
        session = session_from_request(request)
        epoch = 0
        try:
            async with transaction_scope():
                try:
                    principal = principal_from_request(request)
                except _DevFallback as fallback:
                    identity = await person_by_email(fallback.email)
                    if identity is None:
                        raise Unauthenticated from None
                    principal = Principal(
                        person_id=identity.person_id, email=identity.email
                    )
                else:
                    # A validly-signed cookie can outlive both the person it
                    # names (deleted since sealing) and its own authority
                    # (revoked since sealing). One lookup answers both: None
                    # means the row is gone, and a moved-on epoch means every
                    # token sealed before the bump is finished.
                    current = await person_session_epoch(principal.person_id)
                    if current is None or session is None or current != session.epoch:
                        raise Unauthenticated from None
                    epoch = current
                # Armed before the handler rather than inside it, so a handler
                # that reads an identity table sees the same principal every
                # other query does. Handlers that call resolve_space re-arm
                # with the identical value, which is a no-op.
                await arm(principal)
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
        except PageNotFound:
            return JSONResponse({"error": "not_found"}, status_code=404)
        except PrivateContentLeak as error:
            # 409, not 403: the caller is entitled to write here, and the
            # content is theirs. What is refused is this route to it, and the
            # detail names the one that is open.
            return JSONResponse(
                {"error": "private_content", "detail": str(error)}, status_code=409
            )
        except PageTooLarge as error:
            # 400 with the reason: it names something the caller can fix by
            # splitting the page, and a bare "bad_request" reads as a bug in
            # the app rather than as a rule.
            return JSONResponse(
                {"error": "page_too_large", "detail": str(error)}, status_code=400
            )
        except InvalidPath as error:
            # The detail names what is wrong with the path; the editor shows
            # it verbatim, so a generic 400 here would lose the only useful
            # part of the answer.
            return JSONResponse(
                {"error": "bad_request", "detail": str(error)}, status_code=400
            )
        except InviteBudgetExceeded as error:
            # 429 rather than 400: the request is well-formed and would
            # succeed later. The detail names the unlock date, and the UI
            # shows it verbatim -- a generic failure here reads as a bug.
            return JSONResponse(
                {"error": "invite_budget", "detail": str(error)}, status_code=429
            )
        except SpaceError as error:
            return JSONResponse(
                {"error": "space_error", "detail": str(error)}, status_code=400
            )
        response = result if isinstance(result, Response) else JSONResponse(result)
        if response.headers.get(_ACCOUNT_DELETED_HEADER) == "1":
            del response.headers[_ACCOUNT_DELETED_HEADER]
            response.delete_cookie(SESSION_COOKIE)
        else:
            # issued_at is carried from the incoming cookie, never reset: a
            # renewal that restarts it makes the absolute ceiling unreachable
            # for exactly the session that most needs it, one being used
            # every day. See rif.web.session.
            set_session_cookie(
                response,
                principal,
                secure=cookie_secure(),
                sid=session.sid if session else None,
                issued_at=session.issued_at if session else None,
                epoch=epoch,
            )
        return response

    return endpoint


async def _me(request: Request, principal: Principal) -> dict:
    """Return the logged-in person's identity.

    ``avatar`` is a URL rather than the bytes: the picture is read on every
    screen and changes almost never, so serving it from its own cacheable
    endpoint keeps it out of this payload. The ``v`` parameter is the byte
    length, which is enough to break a stale cache when the picture changes
    without leaking anything the owner does not already know.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: person id, email, display name, and avatar URL or None
    """
    person = await Person.objects().where(Person.id == principal.person_id).first()
    avatar = None
    if person is not None and person.avatar_bytes:
        avatar = f"/api/me/avatar?v={len(person.avatar_bytes)}"
    return {
        "person_id": str(principal.person_id),
        "email": principal.email,
        "display_name": person.display_name if person else "",
        "avatar": avatar,
    }


#: Ceiling on a stored avatar. Large enough for a retina-sized square from a
#: phone camera once the browser has downscaled it, small enough that keeping
#: it in the row rather than the object store stays obviously cheap.
AVATAR_MAX_BYTES = 512_000

#: Formats accepted for an avatar. Deliberately no SVG: it is a script
#: carrier, and this endpoint serves bytes back to a browser.
AVATAR_MIMES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


async def _appearances(request: Request, principal: Principal) -> dict:
    """Return how this person has chosen to see each of their coves.

    Its own endpoint rather than a field on ``/api/index``: the index is the
    MCP surface too, and how a cove is tinted in one person's browser is not
    something an assistant should be handed.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: ``{"coves": {slug: {color, glyph}}}``
    """
    return {"coves": await get_appearances(principal)}


async def _set_appearance(request: Request, principal: Principal) -> dict:
    """Record how this person wants to see one cove.

    :param request: the incoming request, carrying the ``space`` path param
        and a JSON body with nullable ``color`` and ``glyph``
    :param principal: the authenticated person
    :raises BadRequest: if either name is not one of the offered choices
    :returns: the stored choice
    """
    space = await resolve_space(principal, request.path_params["space"])
    payload = await _json_body(request)
    try:
        return await set_appearance(
            principal,
            space.id,
            color=_optional_str(payload, "color"),
            glyph=_optional_str(payload, "glyph"),
        )
    except AppearanceError as exc:
        raise BadRequest(str(exc)) from exc


async def _get_avatar(request: Request, principal: Principal) -> Response:
    """Serve the caller's own avatar bytes.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: the image, or a 404 JSON response when none is set
    """
    person = await Person.objects().where(Person.id == principal.person_id).first()
    if person is None or not person.avatar_bytes:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return Response(
        bytes(person.avatar_bytes),
        media_type=person.avatar_mime or "application/octet-stream",
        # Immutable is safe because the URL carries the size as a cache key:
        # a different picture is a different URL.
        headers={"cache-control": "private, max-age=31536000, immutable"},
    )


async def _put_avatar(request: Request, principal: Principal) -> dict:
    """Replace the caller's avatar with a base64-encoded image.

    Base64 in JSON rather than multipart: every other write on this surface
    is JSON, the size ceiling is small enough that the ~33% encoding
    overhead does not matter, and it keeps the CSRF header requirement
    uniform across the API.

    :param request: the incoming request, carrying ``mime`` and ``data``
    :param principal: the authenticated person
    :raises BadRequest: for an unsupported type, undecodable data, or an
        image over :data:`AVATAR_MAX_BYTES`
    :returns: the new avatar URL
    """
    payload = await _json_body(request)
    mime = _require_str(payload, "mime")
    if mime not in AVATAR_MIMES:
        raise BadRequest(f"{mime!r} is not one of {', '.join(sorted(AVATAR_MIMES))}")
    try:
        raw = base64.b64decode(_require_str(payload, "data"), validate=True)
    except (binascii.Error, ValueError):
        raise BadRequest("'data' is not valid base64") from None
    if not raw:
        raise BadRequest("'data' decoded to no bytes")
    if len(raw) > AVATAR_MAX_BYTES:
        raise BadRequest(
            f"a picture may be at most {AVATAR_MAX_BYTES // 1000}kB; "
            f"this one is {len(raw) // 1000}kB"
        )
    await Person.update({Person.avatar_mime: mime, Person.avatar_bytes: raw}).where(
        Person.id == principal.person_id
    )
    return {"avatar": f"/api/me/avatar?v={len(raw)}"}


async def _delete_avatar(request: Request, principal: Principal) -> dict:
    """Drop the caller's avatar, falling the UI back to their initials.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: the cleared avatar field
    """
    await Person.update({Person.avatar_mime: None, Person.avatar_bytes: None}).where(
        Person.id == principal.person_id
    )
    return {"avatar": None}


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


async def _delete_page(request: Request, principal: Principal) -> dict:
    """Delete a page and its history.

    :param request: the incoming request, carrying ``space`` and ``path``
        path params
    :param principal: the authenticated person
    :returns: the deleted path and the number of revisions removed
    """
    return await delete_page(
        principal, request.path_params["space"], request.path_params["path"]
    )


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
    owner = await space_owner(space.id)
    is_owner = space.owner_person_id == principal.person_id
    # No blanking pass here any more: rif_roster returns an address only to
    # the cove's owner, so the rule is applied by Postgres against the armed
    # principal rather than by this handler remembering to strip a field it
    # had already fetched.
    roster = await member_roster(space.id)
    return {
        "members": roster,
        "owner_email": owner["email"] if owner else "",
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


async def _invite_to_reef(request: Request, principal: Principal) -> dict:
    """Invite someone to reef itself, without granting any space.

    Deliberately not under ``/api/spaces/…`` — this invite belongs to no
    space, which is the whole point of it.

    :param request: the incoming request, carrying a JSON body with ``email``
        required and ``display_name`` optional
    :param principal: the authenticated person
    :raises BadRequest: for malformed JSON, a missing ``email``, or a field
        of the wrong type
    :raises InviteBudgetExceeded: if the caller's budget is spent
    :returns: the invite outcome, including the relay text the UI must show
    """
    payload = await _json_body(request)
    email = _require_str(payload, "email")
    display_name = _optional_str(payload, "display_name")
    return await invite_to_reef(principal, email, display_name)


async def _invites_left(request: Request, principal: Principal) -> dict:
    """Report how many reef invites the caller has left.

    Lets the UI show the remaining budget before someone types an address,
    rather than only discovering the ceiling by hitting it.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: the remaining count and the ceiling it counts down from
    """
    return {
        "invites_left": await invites_left(principal),
        "budget": INVITE_BUDGET,
        "window_days": INVITE_WINDOW_DAYS,
    }


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


async def _rename_space(request: Request, principal: Principal) -> dict:
    """Change what the caller calls a cove, for the caller only.

    :param request: the incoming request, carrying a ``space`` path param and
        a JSON body with ``name`` required
    :param principal: the authenticated person
    :raises BadRequest: for a malformed body or a missing ``name``
    :returns: the old and new names
    """
    payload = await _json_body(request)
    return await rename_cove(
        principal, request.path_params["space"], _require_str(payload, "name")
    )


async def _leave_space(request: Request, principal: Principal) -> dict:
    """Leave a shared space, handing it on if the caller owned it.

    :param request: the incoming request, carrying a ``space`` path param
    :param principal: the authenticated person
    :returns: the departure outcome, naming any successor
    """
    return await leave_space(principal, request.path_params["space"])


async def _delete_space(request: Request, principal: Principal) -> Response:
    """Destroy a shared space the caller owns and is alone in.

    Guarded the way account deletion is: a typed confirmation in the body, so
    a stray DELETE cannot take a cove down. The name has to match the cove
    being destroyed rather than a constant, because the mistake worth
    catching here is deleting the wrong one.

    :param request: the incoming request, carrying a ``space`` path param
    :param principal: the authenticated person
    :raises BadRequest: if the typed confirmation does not name this space
    :returns: the deletion outcome, with the bytes erased after the response
    """
    slug = request.path_params["space"]
    payload = await _json_body(request)
    if payload.get("confirmation") != slug:
        raise BadRequest("deleting a cove requires its name as confirmation")
    outcome = await delete_space(principal, slug)
    keys = outcome.pop("file_keys", [])
    # The transaction commits as this handler returns, so the bytes are erased
    # from the background task rather than here -- the same shape the account
    # deletion route uses, and for the same reason.
    return JSONResponse(outcome, background=BackgroundTask(erase_objects, keys))


async def _get_file(request: Request, principal: Principal) -> Response:
    """Redirect to a signed URL for a stored file, after checking visibility.

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
    url = await S3ObjectStore().signed_url(
        key,
        settings.signed_url_ttl_seconds,
        mime=attachment.mime,
        filename=attachment.filename or key.rsplit("/", 1)[-1],
    )
    return RedirectResponse(url, status_code=302)


def _download(content: bytes, filename: str, media_type: str) -> Response:
    """Return bytes as a private browser download."""
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


async def _export(request: Request, principal: Principal) -> Response:
    """Download current content as Markdown ZIP or JSON.

    ``scope=all`` exports every accessible cove; any other value is resolved
    as one cove alias through the same access checks as page reads.

    POST, not GET, and the options travel in the body. Two reasons, both
    about what a GET is: it is reachable by navigation, so a hostile page
    could point a logged-in reader's browser at this route and trigger a
    drive-by download of their whole reef -- the attacker cannot read the
    bytes cross-origin, but the file lands on the victim's disk, and the
    ``Lax`` session cookie is sent on exactly that kind of top-level
    navigation. And a GET puts cove names, which are the user's own words,
    into request lines that proxies and access logs keep. As a POST the
    route needs the ``X-Rif-Csrf`` header, which no cross-origin navigation
    or form can set.

    :param request: the incoming request; ``scope`` and ``format`` come from
        the JSON body
    :param principal: the authenticated person
    :raises BadRequest: for a malformed body or an unknown format
    :returns: the archive as a private download
    """
    payload = await _json_body(request)
    export_format = _optional_str(payload, "format") or "markdown"
    scope = _optional_str(payload, "scope") or "all"
    alias = None if scope == "all" else scope
    name = "all-coves" if alias is None else alias
    if export_format == "markdown":
        return _download(
            await build_markdown_archive(principal, alias),
            f"reef-{name}-markdown.zip",
            "application/zip",
        )
    if export_format == "json":
        return _download(
            await build_json_export(principal, alias),
            f"reef-{name}.json",
            "application/json",
        )
    raise BadRequest("'format' must be 'markdown' or 'json'")


async def _dump(request: Request, principal: Principal) -> Response:
    """Download every portable datum visible to the principal as one ZIP.

    POST for the reason :func:`_export` sets out, and more sharply: this is
    the single request that returns a person's entire reef, page bodies and
    file bytes included.

    :param request: the incoming request, unused
    :param principal: the authenticated person
    :returns: the complete archive as a private download
    """
    return _download(
        await build_full_dump(principal),
        "reef-my-data.zip",
        "application/zip",
    )


async def _delete_account(request: Request, principal: Principal) -> Response:
    """Permanently erase an account after two explicit request guards."""
    payload = await _json_body(request)
    if (
        payload.get("acknowledge_shared") is not True
        or payload.get("confirmation") != "DELETE"
    ):
        raise BadRequest("account deletion requires acknowledgement and DELETE")

    deletion = await delete_account_rows(principal)
    body = {
        "deleted": True,
        "deleted_coves": deletion.deleted_coves,
        "transferred_coves": deletion.transferred_coves,
    }
    sid = session_sid(request)
    if sid:
        query = urlencode(
            {
                "session_id": sid,
                "return_to": (
                    f"{os.environ.get('RIF_BASE_URL', '')}/app/signed-out?deleted=1"
                ),
            }
        )
        body["logout_url"] = f"{WORKOS_LOGOUT_URL}?{query}"
    response = JSONResponse(
        body,
        background=BackgroundTask(erase_objects, deletion.file_keys),
    )
    response.headers[_ACCOUNT_DELETED_HEADER] = "1"
    return response


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
    mcp.custom_route("/api/me/avatar", methods=["GET"])(api(_get_avatar))
    mcp.custom_route("/api/me/avatar", methods=["PUT"])(api(_put_avatar))
    mcp.custom_route("/api/me/avatar", methods=["DELETE"])(api(_delete_avatar))
    mcp.custom_route("/api/appearance", methods=["GET"])(api(_appearances))
    mcp.custom_route("/api/spaces/{space}/appearance", methods=["PUT"])(
        api(_set_appearance)
    )
    mcp.custom_route("/api/index", methods=["GET"])(api(_index))
    mcp.custom_route("/api/pages/{space}/{path:path}", methods=["GET"])(api(_get_page))
    mcp.custom_route("/api/pages/{space}/{path:path}", methods=["PUT"])(api(_put_page))
    mcp.custom_route("/api/pages/{space}/{path:path}", methods=["DELETE"])(
        api(_delete_page)
    )
    mcp.custom_route("/api/files/{space}/{key:path}", methods=["GET"])(api(_get_file))
    # Existing rendered Markdown points here; keep it as a compatibility alias.
    mcp.custom_route("/api/images/{space}/{key:path}", methods=["GET"])(api(_get_file))
    # POST rather than GET: both return the caller's content in bulk, and a
    # GET is reachable by cross-origin navigation. See :func:`_export`.
    mcp.custom_route("/api/export", methods=["POST"])(api(_export))
    mcp.custom_route("/api/export/dump", methods=["POST"])(api(_dump))
    mcp.custom_route("/api/account/delete", methods=["POST"])(api(_delete_account))
    mcp.custom_route("/api/spaces", methods=["POST"])(api(_create_space))
    mcp.custom_route("/api/spaces/{space}/members", methods=["GET"])(
        api(_space_members)
    )
    mcp.custom_route("/api/spaces/{space}/invites", methods=["POST"])(api(_invite))
    mcp.custom_route("/api/invites", methods=["GET"])(api(_invites_left))
    mcp.custom_route("/api/invites", methods=["POST"])(api(_invite_to_reef))
    mcp.custom_route("/api/spaces/{space}/members/{email}", methods=["DELETE"])(
        api(_remove_member)
    )
    mcp.custom_route("/api/spaces/{space}/name", methods=["POST"])(api(_rename_space))
    mcp.custom_route("/api/spaces/{space}/leave", methods=["POST"])(api(_leave_space))
    mcp.custom_route("/api/spaces/{space}", methods=["DELETE"])(api(_delete_space))
