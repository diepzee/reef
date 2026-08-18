"""Static serving: the marketing site at ``/``, the built SPA under ``/app``.

Plain Starlette routes, registered onto the FastMCP server via
``custom_route``: ``GET /`` serves the marketing page out of
``Settings.site_dir`` (falling back to a redirect into ``/app`` if the site
isn't present, e.g. a stripped deployment), ``GET /site/{path:path}`` serves
the site's few assets, and ``GET /app`` / ``GET /app/{path:path}`` serve
files out of ``Settings.static_dir``, falling back to ``index.html`` for any
path that isn't a real file on disk -- which is how a client-side router gets
to own everything under ``/app``. Alongside those, ``GET /favicon.ico``,
``GET /favicon.svg``, and ``GET /apple-touch-icon.png`` serve the reef mark
at the origin root, since favicon fetchers -- including MCP clients like
claude.ai's connector list -- request those paths outside ``/app`` and
nothing else answers them there. ``GET /.well-known/glama.json`` serves the
Glama directory's ownership claim on the same footing: a public, tokenless
document at a fixed path that a third party fetches on its own schedule.

The marketing pages that outside parties address by name get root paths of
their own for the same reason: ``GET /privacy``, ``GET /terms`` and ``GET
/changelog`` for the pages the footer links, and ``GET /robots.txt``, ``GET
/sitemap.xml`` and ``GET /llms.txt`` for the files crawlers fetch at fixed
locations. Their ``/site/*.html`` copies 301 to those paths, so each page
answers at exactly one address.
"""

import re
from pathlib import Path

from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
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

#: Cache policy for HTML. ``no-cache`` does not mean "do not store" -- it
#: means "revalidate before reuse", so a browser still gets a cheap 304 and
#: never runs a stale page. This is load-bearing rather than tidy: the SPA
#: shell names the content-hashed bundle, so a browser holding a stale
#: index.html runs an old frontend against a new backend indefinitely. That
#: is not hypothetical -- it is how a fixed avatar upload went on failing
#: for a person whose browser had cached the shell from before the fix.
#: Sending nothing at all, as this module used to, is the trap: a response
#: with no freshness information may be cached on the browser's own guess,
#: commonly a fraction of its ``Last-Modified`` age.
_HTML_CACHE = "no-cache"

#: Cache policy for a build asset whose name carries a content hash. A new
#: build gives it a new name, so these bytes can never change: caching them
#: for a year is safe, and is the point of hashing the name.
_HASHED_CACHE = "public, max-age=31536000, immutable"

#: Cache policy for everything else -- assets served under a plain name,
#: like the mark and favicon the frontend build copies through unhashed.
#: Those change in place, so freezing them for a year would strand a new
#: one behind an old one.
_PLAIN_CACHE = "public, max-age=3600"

#: A build asset whose name carries a content hash, e.g. ``index-psy8engk.js``.
_HASHED_NAME = re.compile(r"-[A-Za-z0-9_]{8,}\.[A-Za-z0-9]+$")


def _cached(path: Path, media_type: str | None = None) -> FileResponse:
    """Serve a file with the cache policy its name and type earn.

    :param path: the file to serve; assumed already validated by the caller
    :param media_type: an explicit content type, or None to let Starlette
        infer it from the suffix
    :returns: a :class:`FileResponse` carrying ``cache-control``
    """
    if path.suffix.lower() in {".html", ".htm"}:
        policy = _HTML_CACHE
    elif _HASHED_NAME.search(path.name):
        policy = _HASHED_CACHE
    else:
        policy = _PLAIN_CACHE
    return FileResponse(path, media_type=media_type, headers={"cache-control": policy})


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
        return _cached(candidate)
    if index.is_file():
        return _cached(index)
    return PlainTextResponse("frontend not built", status_code=503)


def _serve_site_asset(path: str) -> Response:
    """Serve ``path`` from ``site_dir`` if it is a real file inside it, else 404.

    Same traversal guard as :func:`_serve_or_fallback`, but with no SPA
    fallback: the site is a flat page plus a handful of assets, so a miss
    is a plain 404 rather than ``index.html``. A page that also answers on
    a clean root path redirects there first -- see :data:`_CANONICAL_PAGES`.

    :param path: the request path under ``/site``, already URL-decoded by
        Starlette's path-converter
    :returns: a :class:`FileResponse` for a real file, or a 404 plain-text
        response
    """
    canonical = _CANONICAL_PAGES.get(path)
    if canonical is not None:
        return RedirectResponse(canonical, status_code=301)
    base = Path(get_settings().site_dir).resolve()
    try:
        candidate = (base / path).resolve()
        valid = candidate.is_relative_to(base) and candidate.is_file()
    except (ValueError, OSError):
        valid = False
    if valid:
        return _cached(candidate)
    return PlainTextResponse("not found", status_code=404)


#: Marketing pages that also answer on a clean root path. The same document
#: at two addresses splits the search signal between them, so the ``/site/``
#: copy redirects to the canonical one rather than serving a duplicate.
_CANONICAL_PAGES = {
    "index.html": "/",
    "privacy.html": "/privacy",
    "terms.html": "/terms",
    "changelog.html": "/changelog",
}


def _serve_site_page(filename: str, media_type: str | None = None) -> Response:
    """Serve one named file out of ``site_dir`` at the origin root, or 404.

    These are the paths that have to be stable and guessable from outside
    the site -- the legal notices the footer links from every page, and the
    crawler files (``robots.txt``, ``sitemap.xml``, ``llms.txt``) that
    search engines and AI agents fetch at fixed locations. None of them can
    live under ``/site/`` and still be found.

    :param filename: the file's name inside ``site_dir``
    :param media_type: an explicit content type, or None to let Starlette
        infer it from the suffix
    :returns: a :class:`FileResponse` for the file, or a 404 plain-text
        response when the site tree is absent (same posture as
        :func:`_serve_site_asset`)
    """
    page = Path(get_settings().site_dir) / filename
    if page.is_file():
        return _cached(page, media_type=media_type)
    return PlainTextResponse("Not Found", status_code=404)


def _serve_icon(filename: str, media_type: str) -> Response:
    """Serve a built icon file straight out of ``static_dir``, or 404.

    Favicon fetchers (browsers, and MCP clients like claude.ai's connector
    list) request these paths at the origin root, not under ``/app``, so
    they need their own routes rather than falling through
    :func:`_serve_or_fallback`, which would hand back ``index.html``
    instead of an image.

    :param filename: the unhashed filename under ``static_dir``, as written
        there by the frontend build (see ``frontend/package.json``)
    :param media_type: the MIME type to serve the file as
    :returns: a :class:`FileResponse` for the icon, or a 404 plain-text
        response if the frontend hasn't been built yet -- never a 500
    """
    candidate = Path(get_settings().static_dir) / filename
    if not candidate.is_file():
        return PlainTextResponse("not found", status_code=404)
    return _cached(candidate, media_type=media_type)


def register_static_routes(mcp) -> None:
    """Register the root redirect, favicon, and SPA routes on ``mcp``.

    Idempotent per server instance, keyed by ``id(mcp)`` -- see the module
    docstring on ``_registered`` above for why that's safe here.

    :param mcp: the FastMCP server to register the routes on
    """
    if id(mcp) in _registered:
        return
    _registered.add(id(mcp))

    async def root(request: Request) -> Response:
        """Serve the marketing page, or redirect into the app without one.

        The redirect fallback keeps a deployment without a ``site/`` tree
        (or a misconfigured ``site_dir``) behaving like the pre-site
        builds did, rather than 404ing the origin root.

        :param request: the incoming request
        :returns: the site's ``index.html``, or a 307 redirect to ``/app``
        """
        index = Path(get_settings().site_dir) / "index.html"
        if index.is_file():
            return _cached(index)
        return RedirectResponse("/app", status_code=307)

    async def privacy(request: Request) -> Response:
        """Serve the privacy page at the origin root.

        A root-level path rather than ``/site/privacy.html`` because the
        footer links to it from every page and a legal notice should have a
        stable, guessable URL.

        :param request: the incoming request
        :returns: see :func:`_serve_site_page`
        """
        return _serve_site_page("privacy.html")

    async def terms(request: Request) -> Response:
        """Serve the terms page at the origin root.

        Same footing as :func:`privacy`: a legal notice the footer links
        from every page, so it gets a stable, guessable URL rather than
        ``/site/terms.html``.

        :param request: the incoming request
        :returns: see :func:`_serve_site_page`
        """
        return _serve_site_page("terms.html")

    async def changelog(request: Request) -> Response:
        """Serve the release-notes page at the origin root.

        The footer links it from every page, and it is the one page that
        gains content on every release, so it is worth a clean URL that can
        be shared and indexed rather than ``/site/changelog.html``.

        :param request: the incoming request
        :returns: see :func:`_serve_site_page`
        """
        return _serve_site_page("changelog.html")

    async def robots(request: Request) -> Response:
        """Serve the crawl policy.

        :param request: the incoming request
        :returns: see :func:`_serve_site_page`
        """
        return _serve_site_page("robots.txt", "text/plain; charset=utf-8")

    async def sitemap(request: Request) -> Response:
        """Serve the sitemap the crawl policy points at.

        :param request: the incoming request
        :returns: see :func:`_serve_site_page`
        """
        return _serve_site_page("sitemap.xml", "application/xml")

    async def llms(request: Request) -> Response:
        """Serve the plain-text site summary written for AI agents.

        :param request: the incoming request
        :returns: see :func:`_serve_site_page`
        """
        return _serve_site_page("llms.txt", "text/plain; charset=utf-8")

    async def site_asset(request: Request) -> Response:
        """Serve one of the marketing site's assets.

        :param request: the incoming request; ``path`` is the matched
            ``{path:path}`` route parameter
        :returns: see :func:`_serve_site_asset`
        """
        return _serve_site_asset(request.path_params["path"])

    async def favicon_ico(request: Request) -> Response:
        """Serve the reef mark for ``/favicon.ico``.

        This hands back raw PNG bytes rather than an actual ``.ico``
        container -- modern favicon fetchers accept an ``image/png``
        response at this path just fine, so there's no need to build one.

        :param request: the incoming request
        :returns: see :func:`_serve_icon`
        """
        return _serve_icon("reef-icon.png", "image/png")

    async def apple_touch_icon(request: Request) -> Response:
        """Serve the reef mark for ``/apple-touch-icon.png``.

        :param request: the incoming request
        :returns: see :func:`_serve_icon`
        """
        return _serve_icon("reef-icon.png", "image/png")

    async def favicon_svg(request: Request) -> Response:
        """Serve the reef mark's source SVG for ``/favicon.svg``.

        :param request: the incoming request
        :returns: see :func:`_serve_icon`
        """
        return _serve_icon("reef.svg", "image/svg+xml")

    async def glama_claim(request: Request) -> Response:
        """Serve the Glama ownership claim, or 404 if nobody is configured.

        Glama's directory probes this server anonymously and, because every
        ``/mcp`` request needs a bearer token, can never complete a session
        -- so its listing shows the server as unhealthy no matter how well
        it is running. This document does not change that: it proves who
        maintains the deployment, which is what lets a human control the
        listing and read its monitoring, rather than being an assertion
        about health.

        :param request: the incoming request
        :returns: the claim document, or a 404 plain-text response when
            ``glama_maintainer_email`` is unset
        """
        email = get_settings().glama_maintainer_email
        if not email:
            return PlainTextResponse("Not Found", status_code=404)
        return JSONResponse(
            {
                "$schema": "https://glama.ai/mcp/schemas/connector.json",
                "maintainers": [{"email": email}],
            }
        )

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
    mcp.custom_route("/privacy", methods=["GET"])(privacy)
    mcp.custom_route("/terms", methods=["GET"])(terms)
    mcp.custom_route("/changelog", methods=["GET"])(changelog)
    mcp.custom_route("/robots.txt", methods=["GET"])(robots)
    mcp.custom_route("/sitemap.xml", methods=["GET"])(sitemap)
    mcp.custom_route("/llms.txt", methods=["GET"])(llms)
    mcp.custom_route("/site/{path:path}", methods=["GET"])(site_asset)
    mcp.custom_route("/favicon.ico", methods=["GET"])(favicon_ico)
    mcp.custom_route("/apple-touch-icon.png", methods=["GET"])(apple_touch_icon)
    mcp.custom_route("/favicon.svg", methods=["GET"])(favicon_svg)
    mcp.custom_route("/.well-known/glama.json", methods=["GET"])(glama_claim)
    mcp.custom_route("/app", methods=["GET"])(app_root)
    mcp.custom_route("/app/{path:path}", methods=["GET"])(app_path)
