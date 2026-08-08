"""Static SPA serving: ``/`` redirects to ``/app``, which serves the built frontend.

Two plain Starlette routes, registered onto the FastMCP server via
``custom_route``: ``GET /`` bounces to ``/app``, and ``GET /app`` /
``GET /app/{path:path}`` serve files out of ``Settings.static_dir``, falling
back to ``index.html`` for any path that isn't a real file on disk -- which is
how a client-side router gets to own everything under ``/app``.
"""

from pathlib import Path

from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from rif.config import get_settings

# Servers this module has registered routes on, so repeated calls (as in
# tests that re-import the server module) don't append duplicate Starlette
# routes -- FastMCP has no route-replacement API. This mirrors
# rif.web.routes_auth's _registered set; both assume mcp is a process-wide
# singleton, so keying by id(mcp) never collides with a second live server.
_registered: set[int] = set()


def _serve_or_fallback(path: str) -> Response:
    """Serve ``path`` from ``static_dir`` if it exists there, else index.html.

    The traversal guard resolves the joined path and checks it is still
    inside the resolved static root; anything outside it -- ``..`` segments,
    absolute-path tricks, symlink escapes -- is treated the same as a
    missing file and falls back to the SPA shell rather than ever serving
    something outside the static tree. A path that isn't valid on the
    filesystem at all -- for example an embedded NUL byte from a request
    like ``/app/foo%00.txt``, which ``Path.resolve()`` raises ``ValueError``
    on -- is treated the same way rather than propagating as a 500.

    :param path: the request path under ``/app``, already URL-decoded by
        Starlette's path-converter
    :returns: a :class:`FileResponse` for a real file, the SPA's
        ``index.html``, or a 503 plain-text response if the frontend hasn't
        been built
    """
    base = Path(get_settings().static_dir).resolve()
    index = base / "index.html"
    try:
        candidate = (base / path).resolve()
        valid = candidate.is_relative_to(base) and candidate.is_file()
    except (ValueError, OSError):
        valid = False
    if valid:
        return FileResponse(candidate)
    if index.is_file():
        return FileResponse(index)
    return PlainTextResponse("frontend not built", status_code=503)


def register_static_routes(mcp) -> None:
    """Register the root redirect and SPA routes on ``mcp``.

    Idempotent per server instance, keyed by ``id(mcp)`` -- see the module
    docstring on ``_registered`` above for why that's safe here.

    :param mcp: the FastMCP server to register the routes on
    """
    if id(mcp) in _registered:
        return
    _registered.add(id(mcp))

    async def root(request: Request) -> Response:
        """Redirect the bare root to the SPA entry point.

        :param request: the incoming request
        :returns: a 307 redirect to ``/app``
        """
        return RedirectResponse("/app", status_code=307)

    async def app_root(request: Request) -> Response:
        """Serve the SPA shell for ``/app`` itself.

        :param request: the incoming request
        :returns: see :func:`_serve_or_fallback`
        """
        return _serve_or_fallback("")

    async def app_path(request: Request) -> Response:
        """Serve a static asset or fall back to the SPA shell.

        :param request: the incoming request; ``path`` is the matched
            ``{path:path}`` route parameter
        :returns: see :func:`_serve_or_fallback`
        """
        return _serve_or_fallback(request.path_params["path"])

    mcp.custom_route("/", methods=["GET"])(root)
    mcp.custom_route("/app", methods=["GET"])(app_root)
    mcp.custom_route("/app/{path:path}", methods=["GET"])(app_path)
