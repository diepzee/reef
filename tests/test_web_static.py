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
