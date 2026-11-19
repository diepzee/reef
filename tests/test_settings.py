"""Settings: grouped in code, read under the names deployments already use."""

import importlib.util
from pathlib import Path

import pytest

from reef import config

#: Every variable read before the settings were grouped, by its exact name.
#: A deployment may set any of them. Renaming one would make it read as
#: absent, so none may disappear from the settings.
NAMES_BEFORE_GROUPING = {
    "REEF_DATABASE_URL",
    "REEF_TEST_DATABASE_URL",
    "REEF_MIGRATION_DATABASE_URL",
    "REEF_CONTEXT_CHAR_BUDGET",
    "REEF_PAGE_MAX_CHARS",
    "REEF_S3_ENDPOINT",
    "REEF_S3_BUCKET",
    "REEF_S3_ACCESS_KEY",
    "REEF_S3_SECRET_KEY",
    "REEF_FILE_MAX_BYTES",
    "REEF_SIGNED_URL_TTL_SECONDS",
    "REEF_SESSION_SECRET",
    "REEF_STATIC_DIR",
    "REEF_SITE_DIR",
    "REEF_OPEN_SEATS",
    "REEF_OPEN_UNTIL",
    "REEF_WORKER_POLL_SECONDS",
    "REEF_SEARCH_LOG",
    "REEF_EMBEDDING_URL",
    "REEF_EMBEDDING_MODEL",
    "REEF_EMBEDDING_DAILY_CAP",
    "REEF_EMBEDDING_DELAY_SECONDS",
    "REEF_GLAMA_MAINTAINER_EMAIL",
    "REEF_JWT_SIGNING_KEY",
    "REEF_OAUTH_STORE_DIR",
    "REEF_ALLOWED_CLIENT_REDIRECTS",
    "REEF_BASE_URL",
    "REEF_DEV_INSECURE",
    "REEF_DEV_PRINCIPAL_EMAIL",
    "WORKOS_CLIENT_ID",
    "WORKOS_AUTHKIT_DOMAIN",
    "WORKOS_MCP_CLIENT_ID",
    "WORKOS_MCP_CLIENT_SECRET",
}


@pytest.fixture(autouse=True)
def fresh_settings():
    """`get_settings` is cached; each case needs a fresh read."""
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def _names() -> list[str]:
    return [name for _, name, _, _ in config.variables()]


def test_every_name_a_deployment_may_set_is_still_read():
    assert NAMES_BEFORE_GROUPING <= set(_names())


def test_no_two_settings_share_a_name():
    assert len(_names()) == len(set(_names()))


def test_every_setting_says_what_it_does():
    assert all(description for _, _, _, description in config.variables())


@pytest.mark.parametrize(
    ("variable", "value", "read"),
    [
        ("REEF_PAGE_MAX_CHARS", "7", lambda s: s.pages.max_chars == 7),
        ("REEF_SEARCH_LOG", "1", lambda s: s.search.log is True),
        ("WORKOS_CLIENT_ID", "abc", lambda s: s.auth.workos_client_id == "abc"),
        ("REEF_RERANK_ENABLED", "0", lambda s: s.rerank.enabled is False),
        (
            "REEF_EMBEDDING_CAP_RETRY_SECONDS",
            "60",
            lambda s: s.embedding.cap_retry_seconds == 60,
        ),
    ],
)
def test_a_variable_reaches_its_setting(monkeypatch, variable, value, read):
    monkeypatch.setenv(variable, value)
    assert read(config.get_settings())


def test_a_name_inside_a_group_is_not_a_second_spelling(monkeypatch):
    """``REEF_PAGE_MAX_CHARS`` is the name. ``REEF_MAX_CHARS`` is nothing."""
    monkeypatch.setenv("REEF_MAX_CHARS", "9")
    assert config.get_settings().pages.max_chars == 200_000


def test_a_variable_named_like_a_group_cannot_stop_the_app(monkeypatch):
    """Only the groups read the environment, never the top level."""
    monkeypatch.setenv("REEF_RERANK", "0")
    monkeypatch.setenv("REEF_SEARCH", "junk")
    assert config.get_settings().rerank.enabled is True


@pytest.mark.parametrize(
    ("value", "on"), [("1", True), ("true", False), ("yes", False), ("0", False)]
)
def test_the_dev_escape_hatch_opens_for_exactly_one(monkeypatch, value, on):
    monkeypatch.setenv("REEF_DEV_INSECURE", value)
    assert config.get_settings().dev.insecure is on


def test_the_runbook_lists_every_setting_as_it_is():
    path = Path(__file__).resolve().parent.parent / "scripts" / "settings_table.py"
    spec = importlib.util.spec_from_file_location("settings_table", path)
    table = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(table)
    found = table.current(table.RUNBOOK.read_text())
    assert found == table.section(), "run `just settings-table`"
