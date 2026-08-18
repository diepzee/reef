"""Static serving: SPA fallback, traversal guard, root redirect."""

import json
import re
from pathlib import Path

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


def test_marketing_site_offers_cli_and_agent_skill_setup():
    """The terminal-first path is visible and gives both working entry points."""
    page = (Path(__file__).parents[1] / "site" / "index.html").read_text()
    assert '<option value="cli"' in page
    assert "uv tool install reef-cli" in page
    assert "npm install -g @haai/reef-cli" in page
    assert "reef login" in page
    assert "github.com/diepzee/rif/tree/main/skills/reef" in page


def test_marketing_site_leaves_the_door_sentence_to_the_server():
    """The landing page carries the slot, never a hardcoded promise.

    Pinning the slot is what catches the drift that matters: a copy edit
    that writes a door state back into the file would sail past a test
    asserting on rendered output, and start lying the day the door shuts.
    """
    page = (Path(__file__).parents[1] / "site" / "index.html").read_text()
    assert "__DOOR__" in page
    assert "no sign-up" not in page.lower()


def _site_with_slot(tmp_path) -> None:
    """Write a marketing page carrying the door slot into ``site_dir``.

    :param tmp_path: the fixture's scratch directory
    """
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text("<!doctype html><title>reef</title>__DOOR__")


def _door(monkeypatch, *, is_open: bool) -> None:
    """Force the door open or shut for the static routes.

    Patches the policy rather than the environment: ``static_client``
    patches the cached settings object, and the env path would need that
    cache cleared out from under it.

    :param monkeypatch: pytest's patcher
    :param is_open: the state to force
    """
    from rif.opendoor import DoorPolicy
    from rif.web import static

    monkeypatch.setattr(static, "door_policy", lambda: DoorPolicy(is_open, 500, ""))


async def test_root_names_the_open_door_while_it_is_open(
    static_client, tmp_path, monkeypatch
):
    """An open door invites the visitor in, and says it is the exception."""
    _site_with_slot(tmp_path)
    _door(monkeypatch, is_open=True)
    response = await static_client.get("/")
    assert response.status_code == 200
    assert "take one while they last" in response.text
    assert "__DOOR__" not in response.text


async def test_root_keeps_the_invite_only_line_once_the_door_shuts(
    static_client, tmp_path, monkeypatch
):
    """The steady state promises no sign-up, which is then true."""
    _site_with_slot(tmp_path)
    _door(monkeypatch, is_open=False)
    response = await static_client.get("/")
    assert "There is no sign-up." in response.text
    assert "__DOOR__" not in response.text


async def test_root_is_never_reused_across_a_door_that_changed(
    static_client, tmp_path, monkeypatch
):
    """No validator derived from the file, so a 304 cannot strand old copy."""
    _site_with_slot(tmp_path)
    _door(monkeypatch, is_open=True)
    response = await static_client.get("/")
    assert response.headers["cache-control"] == "no-cache"
    assert "etag" not in response.headers
    assert "last-modified" not in response.headers


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


async def test_terms_page_is_served_at_the_root(static_client, tmp_path):
    """The footer links /terms from every page, so it must resolve."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "terms.html").write_text("<!doctype html><title>reef terms</title>")
    response = await static_client.get("/terms")
    assert response.status_code == 200
    assert "reef terms" in response.text


async def test_terms_404s_without_a_site_tree(static_client):
    """No site/ packaged: a clean 404 rather than a 500."""
    response = await static_client.get("/terms")
    assert response.status_code == 404


async def test_glama_claim_served_when_configured(static_client, monkeypatch):
    """The claim document names the configured maintainer, unauthenticated.

    Glama fetches this anonymously -- it is the one thing about this server
    it *can* read without a token -- so the assertion that matters is that
    an unauthenticated client gets the document, not a 401.
    """
    from rif.config import get_settings

    monkeypatch.setattr(get_settings(), "glama_maintainer_email", "a@example.com")
    response = await static_client.get("/.well-known/glama.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "$schema": "https://glama.ai/mcp/schemas/connector.json",
        "maintainers": [{"email": "a@example.com"}],
    }


async def test_glama_claim_404s_when_unconfigured(static_client):
    """With no maintainer set, there is nobody to claim it: 404, not an empty claim.

    A document with an empty ``maintainers`` array would fail Glama's own
    schema, so serving one would be worse than serving nothing.
    """
    response = await static_client.get("/.well-known/glama.json")
    assert response.status_code == 404


async def test_the_spa_shell_is_never_served_stale(static_client):
    """``index.html`` must be revalidated, not cached on a browser's guess.

    The shell names the content-hashed bundle, so a stale copy pins a
    browser to an old frontend against a new backend -- which is exactly
    how a fixed avatar upload kept failing after the fix had deployed. A
    response carrying no freshness information at all is the trap: browsers
    may then cache it heuristically off ``Last-Modified``.
    """
    response = await static_client.get("/app")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


async def test_a_content_hashed_asset_is_immutable(static_client, tmp_path):
    """A hashed name can never change its bytes, so it is cached hard."""
    (tmp_path / "index-a1b2c3d4.js").write_text("console.log(2)")
    response = await static_client.get("/app/index-a1b2c3d4.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


async def test_an_unhashed_asset_is_not_frozen(static_client):
    """The mark and favicon are copied under plain names and change in place."""
    response = await static_client.get("/app/app.js")
    assert response.status_code == 200
    assert "immutable" not in response.headers["cache-control"]


async def test_changelog_page_is_served_at_the_root(static_client, tmp_path):
    """The footer links /changelog, and it is the page that keeps changing."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "changelog.html").write_text("<!doctype html><title>reef changelog</title>")
    response = await static_client.get("/changelog")
    assert response.status_code == 200
    assert "reef changelog" in response.text


async def test_changelog_404s_without_a_site_tree(static_client):
    """No site/ packaged: a clean 404 rather than a 500."""
    response = await static_client.get("/changelog")
    assert response.status_code == 404


async def test_crawler_files_are_served_at_the_root(static_client, tmp_path):
    """robots.txt, sitemap.xml and llms.txt are fetched at fixed paths.

    A crawler asks the origin root for these by name and never looks under
    ``/site/``, so serving them anywhere else is the same as not having
    them.
    """
    site = tmp_path / "site"
    site.mkdir()
    (site / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    (site / "sitemap.xml").write_text("<urlset></urlset>")
    (site / "llms.txt").write_text("# reef\n")
    for path, fragment in (
        ("/robots.txt", "User-agent"),
        ("/sitemap.xml", "urlset"),
        ("/llms.txt", "# reef"),
    ):
        response = await static_client.get(path)
        assert response.status_code == 200, path
        assert fragment in response.text, path


async def test_crawler_files_404_without_a_site_tree(static_client):
    """No site/ packaged: a clean 404 rather than a 500."""
    for path in ("/robots.txt", "/sitemap.xml", "/llms.txt"):
        assert (await static_client.get(path)).status_code == 404, path


async def test_the_site_copy_of_a_page_redirects_to_its_clean_url(
    static_client, tmp_path
):
    """One document, one address.

    Every page under ``site/`` is reachable through ``/site/{path}`` as
    well as its own root path. Left alone that is duplicate content: two
    URLs serving identical bytes, with the search signal split between
    them and no way for a crawler to know which one is meant.
    """
    site = tmp_path / "site"
    site.mkdir()
    for name in ("index.html", "privacy.html", "terms.html", "changelog.html"):
        (site / name).write_text("<!doctype html>")
    for name, clean in (
        ("index.html", "/"),
        ("privacy.html", "/privacy"),
        ("terms.html", "/terms"),
        ("changelog.html", "/changelog"),
    ):
        response = await static_client.get(f"/site/{name}", follow_redirects=False)
        assert response.status_code == 301, name
        assert response.headers["location"] == clean, name


async def test_a_plain_site_asset_still_serves(static_client, tmp_path):
    """Only the pages with clean URLs redirect; assets are untouched."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "nunito-latin.woff2").write_bytes(b"wOF2")
    response = await static_client.get("/site/nunito-latin.woff2")
    assert response.status_code == 200


def test_every_marketing_page_declares_one_canonical_url():
    """Duplicate URLs are only harmless while a canonical says which wins."""
    site = Path(__file__).parents[1] / "site"
    for name, url in (
        ("index.html", "https://reefwith.me/"),
        ("privacy.html", "https://reefwith.me/privacy"),
        ("terms.html", "https://reefwith.me/terms"),
        ("changelog.html", "https://reefwith.me/changelog"),
    ):
        page = (site / name).read_text()
        assert page.count('<link rel="canonical"') == 1, name
        assert f'<link rel="canonical" href="{url}">' in page, name
        assert f'<meta property="og:url" content="{url}">' in page, name


def test_every_marketing_page_renders_a_share_card():
    """A bare link is the default; a launch post cannot afford one.

    The card is what a share on X, Hacker News, Slack or iMessage renders,
    and those shares are where a new domain's first links come from.
    """
    site = Path(__file__).parents[1] / "site"
    assert (site / "og-card.png").is_file()
    for name in ("index.html", "privacy.html", "terms.html", "changelog.html"):
        page = (site / name).read_text()
        assert 'content="https://reefwith.me/site/og-card.png"' in page, name
        assert '<meta name="twitter:card" content="summary_large_image">' in page, name


def test_the_landing_page_describes_itself_to_search_engines():
    """Structured data, and a title carrying a term somebody would type.

    "reef" alone is unwinnable -- the query belongs to sandals, aquariums
    and actual coral -- so the title has to say what the thing is.
    """
    page = (Path(__file__).parents[1] / "site" / "index.html").read_text()
    block = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL
    )
    graph = json.loads(block.group(1))["@graph"]
    assert {entry["@type"] for entry in graph} == {
        "WebSite",
        "SoftwareApplication",
        "Organization",
    }
    title = re.search(r"<title>(.*?)</title>", page).group(1)
    assert "memory" in title.lower()


def test_the_crawl_policy_lets_search_and_grounding_in():
    """Findable from inside an assistant is the point; training is not granted."""
    robots = (Path(__file__).parents[1] / "site" / "robots.txt").read_text()
    assert "search=yes" in robots
    assert "ai-input=yes" in robots
    assert "ai-train=no" in robots
    assert "Sitemap: https://reefwith.me/sitemap.xml" in robots


def test_the_sitemap_lists_exactly_the_canonical_pages():
    """A sitemap that names a redirecting or missing URL trains Google to
    distrust the whole file, so it tracks the clean paths and nothing else.
    """
    sitemap = (Path(__file__).parents[1] / "site" / "sitemap.xml").read_text()
    assert set(re.findall(r"<loc>(.*?)</loc>", sitemap)) == {
        "https://reefwith.me/",
        "https://reefwith.me/changelog",
        "https://reefwith.me/privacy",
        "https://reefwith.me/terms",
    }


def test_the_spa_shell_asks_not_to_be_indexed():
    """Every /app route renders one empty shell: a blank page in the index."""
    shell = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert '<meta name="robots" content="noindex, follow" />' in shell
