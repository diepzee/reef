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
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_root_redirects_to_app(static_client):
    """``GET /`` redirects to ``/app`` with a 307."""
    response = await static_client.get("/")
    assert response.status_code == 307
    assert response.headers["location"] == "/app"


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
