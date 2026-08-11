"""Static serving: SPA fallback, traversal guard, root redirect."""

import httpx
import pytest_asyncio

from rif.server import mcp  # importing server registers all web routes


@pytest_asyncio.fixture
async def static_client(monkeypatch, tmp_path):
    """Point ``static_dir`` at a scratch dir with a fake built SPA.

    :returns: an ``httpx.AsyncClient`` wired to the in-process ASGI app
    """
    from rif.config import get_settings

    (tmp_path / "index.html").write_text("<!doctype html><title>rif</title>")
    (tmp_path / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(get_settings(), "static_dir", str(tmp_path))
    # Point site_dir away from the repo's real site/ so root-route tests
    # exercise the no-site fallback unless they build a site themselves.
    site = tmp_path / "site"
    monkeypatch.setattr(get_settings(), "site_dir", str(site))
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_root_serves_marketing_site(static_client, tmp_path):
    """``GET /`` serves ``site_dir``'s index.html when it exists."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<!doctype html><title>reef marketing</title>")
    response = await static_client.get("/")
    assert response.status_code == 200
    assert "reef marketing" in response.text


async def test_root_redirects_to_app_without_site(static_client):
    """``GET /`` falls back to a 307 into ``/app`` when no site is present."""
    response = await static_client.get("/")
    assert response.status_code == 307
    assert response.headers["location"] == "/app"


async def test_site_asset_served(static_client, tmp_path):
    """A real file under ``site_dir`` is served via ``/site/{path}``."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "hero.png").write_bytes(b"\x89PNG fake")
    response = await static_client.get("/site/hero.png")
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


async def test_site_asset_miss_is_404(static_client, tmp_path):
    """A missing site asset 404s rather than falling back to any index."""
    (tmp_path / "site").mkdir()
    response = await static_client.get("/site/nope.png")
    assert response.status_code == 404


async def test_site_asset_traversal_blocked(static_client, tmp_path):
    """A traversal attempt under ``/site`` never escapes ``site_dir``."""
    (tmp_path / "site").mkdir()
    response = await static_client.get("/site/..%2Findex.html")
    assert response.status_code == 404


async def test_asset_served(static_client):
    """A real file under ``static_dir`` is served as-is."""
    response = await static_client.get("/app/app.js")
    assert response.status_code == 200
    assert "console" in response.text


async def test_spa_fallback(static_client):
    """A client-side route with no matching file falls back to index.html."""
    response = await static_client.get("/app/spaces/team/pages/notes.md")
    assert response.status_code == 200
    assert "<title>rif</title>" in response.text


async def test_traversal_blocked(static_client):
    """A traversal attempt never escapes ``static_dir``; it falls back to index."""
    response = await static_client.get("/app/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 200  # falls back to index, never the file
    assert "<title>rif</title>" in response.text


async def test_embedded_nul_falls_back(static_client):
    """A path with an embedded NUL byte never 500s; it falls back to index.

    ``Path.resolve()`` raises ``ValueError`` on an embedded NUL, which must
    be caught and treated like any other invalid candidate rather than
    escaping as an unhandled exception.
    """
    response = await static_client.get("/app/foo%00.txt")
    assert response.status_code == 200
    assert "<title>rif</title>" in response.text


@pytest_asyncio.fixture
async def static_client_with_icons(monkeypatch, tmp_path):
    """Like ``static_client``, but with the reef icon files also on disk.

    :returns: an ``httpx.AsyncClient`` wired to the in-process ASGI app,
        with a built SPA and favicon assets both present in ``static_dir``
    """
    from rif.config import get_settings

    (tmp_path / "index.html").write_text("<!doctype html><title>rif</title>")
    (tmp_path / "reef-icon.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")
    (tmp_path / "reef.svg").write_text("<svg><!-- reef --></svg>")
    monkeypatch.setattr(get_settings(), "static_dir", str(tmp_path))
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_favicon_ico_served_as_png(static_client_with_icons):
    """``GET /favicon.ico`` serves the reef PNG with an ``image/png`` type."""
    response = await static_client_with_icons.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


async def test_apple_touch_icon_served(static_client_with_icons):
    """``GET /apple-touch-icon.png`` serves the reef PNG."""
    response = await static_client_with_icons.get("/apple-touch-icon.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


async def test_favicon_svg_served(static_client_with_icons):
    """``GET /favicon.svg`` serves the reef SVG with an ``image/svg+xml`` type."""
    response = await static_client_with_icons.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"


async def test_favicon_404_when_frontend_not_built(static_client):
    """With no icon files on disk, favicon routes 404 rather than 500.

    ``static_client`` only writes ``index.html`` and ``app.js`` -- no reef
    icons -- mirroring a static dir from a frontend build that predates
    this feature, or one that hasn't run yet.
    """
    response = await static_client.get("/favicon.ico")
    assert response.status_code == 404


async def test_privacy_page_is_served_at_the_root(static_client, tmp_path):
    """The footer links /privacy from every page, so it must resolve."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "privacy.html").write_text("<!doctype html><title>reef privacy</title>")
    response = await static_client.get("/privacy")
    assert response.status_code == 200
    assert "reef privacy" in response.text


async def test_privacy_404s_without_a_site_tree(static_client):
    """No site/ packaged: a clean 404 rather than a 500."""
    response = await static_client.get("/privacy")
    assert response.status_code == 404
