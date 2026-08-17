"""The what's-new feed: what it shows, and whose mark it moves."""

import json

import pytest
from conftest import _login

from rif.config import get_settings

CSRF = {"X-Rif-Csrf": "1"}

FEED = {
    "entries": [
        {
            "version": "0.10.0",
            "date": "2026-08-18",
            "changes": [
                {"kind": "added", "text": "Search your pages from your assistant."}
            ],
        },
        {
            "version": "0.9.0",
            "date": "2026-08-01",
            "changes": [{"kind": "fixed", "text": "Pictures upload again."}],
        },
    ]
}


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """Point the server's site directory at a temporary feed.

    :param tmp_path: pytest's per-test directory
    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the directory the feed was written into
    """
    (tmp_path / "release-notes.json").write_text(json.dumps(FEED))
    # site_dir is a str on Settings, not a Path -- see src/rif/config.py.
    monkeypatch.setattr(get_settings(), "site_dir", str(tmp_path))
    return tmp_path


async def test_the_feed_is_returned_newest_first(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.get("/api/release-notes")
    assert response.status_code == 200
    body = response.json()
    assert [entry["version"] for entry in body["entries"]] == ["0.10.0", "0.9.0"]


async def test_a_person_who_has_read_nothing_has_unread(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    assert (await api.get("/api/release-notes")).json()["unread"] is True


async def test_marking_seen_clears_it(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    stamped = await api.post("/api/release-notes/seen", headers=CSRF)
    assert stamped.status_code == 200
    assert stamped.json() == {"unread": False}
    assert (await api.get("/api/release-notes")).json()["unread"] is False


async def test_marking_seen_twice_is_harmless(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    await api.post("/api/release-notes/seen", headers=CSRF)
    again = await api.post("/api/release-notes/seen", headers=CSRF)
    assert again.status_code == 200
    assert (await api.get("/api/release-notes")).json()["unread"] is False


async def test_an_older_mark_is_still_unread(api, world, feed, seed):
    """0.9.0 is older than 0.10.0 -- the case a string compare gets wrong."""
    alice, _bob, _ = world
    await seed.execute(
        "UPDATE persons SET last_seen_release = '0.9.0' WHERE id = $1", alice.id
    )
    _login(api, alice)
    assert (await api.get("/api/release-notes")).json()["unread"] is True


async def test_a_missing_feed_is_empty_rather_than_broken(
    api, world, tmp_path, monkeypatch
):
    """Before the first release there is no file, and the panel must open."""
    monkeypatch.setattr(get_settings(), "site_dir", str(tmp_path))
    alice, _bob, _ = world
    _login(api, alice)
    response = await api.get("/api/release-notes")
    assert response.status_code == 200
    assert response.json() == {"entries": [], "unread": False}


async def test_one_persons_mark_does_not_move_anothers(api, world, feed, seed):
    """The mark is per person, and persons is self-only under RLS."""
    alice, bob, _ = world
    _login(api, alice)
    await api.post("/api/release-notes/seen", headers=CSRF)

    _login(api, bob)
    assert (await api.get("/api/release-notes")).json()["unread"] is True

    # Read past the policies to prove the write landed on exactly one row.
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", alice.id
        )
        == "0.10.0"
    )
    assert (
        await seed.fetchval(
            "SELECT last_seen_release FROM persons WHERE id = $1", bob.id
        )
        is None
    )


async def test_marking_seen_needs_the_csrf_header(api, world, feed):
    alice, _bob, _ = world
    _login(api, alice)
    assert (await api.post("/api/release-notes/seen")).status_code == 403


async def test_a_stranger_gets_nothing(api, feed):
    assert (await api.get("/api/release-notes")).status_code == 401
