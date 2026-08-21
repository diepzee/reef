"""What a connector list draws for reef, and why the URL carries a version.

An icon is advertised as a URL, and every client that fetches one is
entitled to keep it. An unversioned address therefore has no way to say the
mark changed: the client asks for ``/favicon.svg`` again and its old copy is
still a correct answer. Stamping the version into the query is what makes a
new mark a new URL, and the only lever we have over caches we do not own.
"""

from reef.server import APP_VERSION, _brand_icons


def test_no_origin_means_no_icons(monkeypatch):
    """Relative icon srcs would be broken ones, so offer none at all."""
    monkeypatch.delenv("REEF_BASE_URL", raising=False)
    monkeypatch.delenv("RIF_BASE_URL", raising=False)
    assert _brand_icons() is None


def test_icons_are_absolute_and_carry_the_version(monkeypatch):
    monkeypatch.setenv("REEF_BASE_URL", "https://reefwith.me/")
    assert [icon.src for icon in _brand_icons()] == [
        f"https://reefwith.me/favicon.svg?v={APP_VERSION}",
        f"https://reefwith.me/apple-touch-icon.png?v={APP_VERSION}",
    ]


def test_the_version_is_the_one_the_release_stamps():
    """Not a constant of its own: a second number would drift from the first."""
    import tomllib
    from pathlib import Path

    manifest = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert APP_VERSION == tomllib.loads(manifest.read_text())["project"]["version"]
