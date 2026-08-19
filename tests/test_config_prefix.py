"""Settings answer to REEF_, and still to RIF_ until an operator has moved on.

Twelve RIF_-prefixed variables are set on Railway production, including the
JWT signing key and the pair holding the open door. Renaming the prefix in
code while the deployment still spells them the old way would make every one
of them read as absent — and absent config is, correctly, a reason this
codebase refuses to boot.

So both prefixes work, REEF_ wins when both are set, and the process can say
which legacy names it is still relying on. That last part is what makes
removing the fallback an evidenced decision rather than a guess.
"""

import pytest

from reef import config


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """`get_settings` is cached; each case needs a fresh read."""
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_the_new_prefix_is_read(monkeypatch):
    monkeypatch.setenv("REEF_OPEN_SEATS", "7")
    assert config.get_settings().open_seats == 7


def test_the_old_prefix_still_works(monkeypatch):
    """A Railway variable nobody has renamed yet must keep working."""
    monkeypatch.delenv("REEF_OPEN_SEATS", raising=False)
    monkeypatch.setenv("RIF_OPEN_SEATS", "5")
    assert config.get_settings().open_seats == 5


def test_the_new_prefix_wins_when_both_are_set(monkeypatch):
    """During a cutover both exist; the one being moved to decides."""
    monkeypatch.setenv("RIF_OPEN_SEATS", "5")
    monkeypatch.setenv("REEF_OPEN_SEATS", "7")
    assert config.get_settings().open_seats == 7


def test_legacy_names_in_use_are_reported(monkeypatch):
    """The signal that says when the fallback is safe to delete."""
    for name in list(config.os.environ):
        if name.startswith(("RIF_", "REEF_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RIF_OPEN_SEATS", "5")
    monkeypatch.setenv("REEF_SESSION_SECRET", "x")
    assert config.legacy_environment_names() == ["RIF_OPEN_SEATS"]


def test_a_legacy_name_with_a_new_equivalent_is_not_reported(monkeypatch):
    """Both spellings set means the operator has already moved that one."""
    for name in list(config.os.environ):
        if name.startswith(("RIF_", "REEF_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RIF_OPEN_SEATS", "5")
    monkeypatch.setenv("REEF_OPEN_SEATS", "7")
    assert config.legacy_environment_names() == []


def test_nothing_reported_when_the_environment_is_already_moved(monkeypatch):
    for name in list(config.os.environ):
        if name.startswith("RIF_"):
            monkeypatch.delenv(name, raising=False)
    assert config.legacy_environment_names() == []


def test_direct_reads_honour_both_prefixes(monkeypatch):
    """Not everything goes through Settings; these are read by hand."""
    monkeypatch.delenv("REEF_DEV_INSECURE", raising=False)
    monkeypatch.setenv("RIF_DEV_INSECURE", "1")
    assert config.env("DEV_INSECURE") == "1"
    monkeypatch.setenv("REEF_DEV_INSECURE", "0")
    assert config.env("DEV_INSECURE") == "0"


def test_direct_read_returns_none_when_neither_is_set(monkeypatch):
    monkeypatch.delenv("REEF_BASE_URL", raising=False)
    monkeypatch.delenv("RIF_BASE_URL", raising=False)
    assert config.env("BASE_URL") is None
