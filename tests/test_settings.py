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
    "REEF_RERANK_MIN_PAGES",
    "REEF_EMBEDDING_PAGE_SECONDS",
    "REEF_EMBEDDING_PAGE_CHARS",
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
    "REEF_EMBEDDING_SECONDS",
    "REEF_RERANK_URL",
    "REEF_RERANK_MODEL",
    "REEF_RERANK_ENABLED",
    "REEF_RERANK_CANDIDATES",
    "REEF_RERANK_PASSAGES",
    "REEF_RERANK_SECONDS",
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


def _aliases(info) -> list[str]:
    """Every spelling a field answers to, the first being the one reported."""
    alias = info.validation_alias
    if alias is None:
        return []
    if isinstance(alias, str):
        return [alias]
    return list(alias.choices)


def _spellings() -> set[str]:
    """Every variable name any setting is read under, reported or older."""
    found = set()
    for holder in config.Settings.model_fields.values():
        model = holder.default_factory
        prefix = model.model_config.get("env_prefix", "")
        for name, info in model.model_fields.items():
            found.update(_aliases(info) or [f"{prefix}{name}".upper()])
    return found


def test_every_name_a_deployment_may_set_is_still_read():
    assert NAMES_BEFORE_GROUPING <= _spellings()


def _sample(default: object) -> tuple[str, object]:
    """Pick a value unlike the default, as a variable and as read back."""
    if isinstance(default, bool):
        return ("0", False) if default else ("1", True)
    if isinstance(default, int):
        return str(default + 1), default + 1
    if isinstance(default, float):
        return str(default + 1.0), default + 1.0
    return "probe-value", "probe-value"


def _fields():
    """Every setting: its group, field name, variable name and default."""
    for group, holder in config.Settings.model_fields.items():
        model = holder.default_factory
        prefix = model.model_config.get("env_prefix", "")
        for name, info in model.model_fields.items():
            choices = _aliases(info) or [f"{prefix}{name}".upper()]
            for variable in choices:
                yield group, name, variable, info.default


@pytest.mark.parametrize(
    ("group", "name", "variable", "default"),
    list(_fields()),
    ids=[variable for _, _, variable, _ in _fields()],
)
@pytest.mark.parametrize("legacy", [False, True], ids=["REEF", "RIF"])
def test_every_variable_reaches_its_setting(
    monkeypatch, group, name, variable, default, legacy
):
    """Set it for real and read it back, under both prefixes."""
    if legacy and not variable.startswith(config.PREFIX):
        pytest.skip("not a reef name, so it has no legacy spelling")
    raw, expected = _sample(default)
    spelled = (
        config.LEGACY_PREFIX + variable[len(config.PREFIX) :] if legacy else variable
    )
    monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(spelled, raw)
    assert getattr(getattr(config.get_settings(), group), name) == expected


def test_no_two_settings_share_a_name():
    assert len(_names()) == len(set(_names()))


def test_every_setting_says_what_it_does():
    assert all(description for _, _, _, description in config.variables())


@pytest.mark.parametrize(
    ("variable", "value", "read"),
    [
        ("REEF_ENGRAM_MAX_CHARS", "70000", lambda s: s.engrams.max_chars == 70000),
        ("REEF_RERANK_MIN_ENGRAMS", "7", lambda s: s.rerank.min_engrams == 7),
        ("REEF_SEARCH_LOG", "1", lambda s: s.search.log is True),
        ("WORKOS_CLIENT_ID", "abc", lambda s: s.auth.workos_client_id == "abc"),
        ("REEF_RERANK_ENABLED", "0", lambda s: s.rerank.enabled is False),
        (
            "REEF_EMBEDDING_CAP_RETRY_SECONDS",
            "60",
            lambda s: s.embedding.cap_retry_seconds == 60,
        ),
        ("REEF_FILE_TEXT", "1", lambda s: s.reading.enabled is True),
    ],
)
def test_a_variable_reaches_its_setting(monkeypatch, variable, value, read):
    monkeypatch.setenv(variable, value)
    assert read(config.get_settings())


def test_file_text_is_off_by_default(monkeypatch):
    """Reef reads no file's text until the operator switches it on."""
    monkeypatch.delenv("REEF_FILE_TEXT", raising=False)
    monkeypatch.delenv("RIF_FILE_TEXT", raising=False)
    assert config.get_settings().reading.enabled is False


def test_a_name_inside_a_group_is_not_a_second_spelling(monkeypatch):
    """``REEF_ENGRAM_MAX_CHARS`` is the name. ``REEF_MAX_CHARS`` is nothing."""
    monkeypatch.setenv("REEF_MAX_CHARS", "9")
    assert config.get_settings().engrams.max_chars == 200_000


def test_old_engram_cap_name_still_read(monkeypatch):
    """A deployment that still sets ``REEF_PAGE_MAX_CHARS`` keeps its cap."""
    monkeypatch.delenv("REEF_ENGRAM_MAX_CHARS", raising=False)
    monkeypatch.setenv("REEF_PAGE_MAX_CHARS", "70000")
    assert config.get_settings().engrams.max_chars == 70000


def test_old_rerank_floor_name_still_read(monkeypatch):
    monkeypatch.delenv("REEF_RERANK_MIN_ENGRAMS", raising=False)
    monkeypatch.setenv("REEF_RERANK_MIN_PAGES", "12")
    assert config.get_settings().rerank.min_engrams == 12


def test_new_name_wins_over_old(monkeypatch):
    monkeypatch.setenv("REEF_PAGE_MAX_CHARS", "70000")
    monkeypatch.setenv("REEF_ENGRAM_MAX_CHARS", "90000")
    monkeypatch.setenv("REEF_RERANK_MIN_PAGES", "12")
    monkeypatch.setenv("REEF_RERANK_MIN_ENGRAMS", "34")
    settings = config.get_settings()
    assert settings.engrams.max_chars == 90000
    assert settings.rerank.min_engrams == 34


def test_the_old_names_arrive_through_the_legacy_prefix_too(monkeypatch):
    monkeypatch.delenv("REEF_ENGRAM_MAX_CHARS", raising=False)
    monkeypatch.delenv("REEF_PAGE_MAX_CHARS", raising=False)
    monkeypatch.setenv("RIF_ENGRAM_MAX_CHARS", "80000")
    assert config.get_settings().engrams.max_chars == 80000


def test_a_variable_named_like_a_group_cannot_stop_the_app(monkeypatch):
    """Only the groups read the environment, never the top level."""
    monkeypatch.setenv("REEF_RERANK", "0")
    monkeypatch.setenv("REEF_SEARCH", "junk")
    assert config.get_settings().rerank.enabled is True


@pytest.mark.parametrize(
    ("value", "on"),
    [
        ("1", True),
        ("true", False),
        ("yes", False),
        ("0", False),
        (" 1", False),
        ("1\n", False),
    ],
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


def test_the_dev_escape_hatch_wants_its_exact_name(monkeypatch):
    """``reef_dev_insecure`` is not ``REEF_DEV_INSECURE``, and cannot outvote it."""
    monkeypatch.setenv("REEF_DEV_INSECURE", "0")
    monkeypatch.setenv("reef_dev_insecure", "1")
    assert config.get_settings().dev.insecure is False


@pytest.mark.parametrize(
    "variable",
    [
        "REEF_EMBEDDING_PIECE_MAX_CHARS",
        "REEF_EMBEDDING_BATCH",
        "REEF_RERANK_CANDIDATES",
        "REEF_USAGE_WINDOW",
        "REEF_SEARCH_MAX_RESULTS",
    ],
)
def test_a_value_that_would_break_search_is_refused(monkeypatch, variable):
    """Zero here hangs or breaks a search; better not to start at all."""
    monkeypatch.setenv(variable, "0")
    with pytest.raises(ValueError):
        config.get_settings()


def test_a_page_longer_than_an_engram_is_refused(monkeypatch):
    """``open`` cuts at a page, so a page must fit the engram cap."""
    monkeypatch.setenv("REEF_ENGRAM_MAX_CHARS", "10000")
    monkeypatch.setenv("REEF_FILE_TEXT_PAGE_CHARS", "10001")
    with pytest.raises(ValueError, match="PAGE_CHARS"):
        config.Settings()
    monkeypatch.setenv("REEF_FILE_TEXT_PAGE_CHARS", "10000")
    assert config.Settings().reading.page_chars == 10_000


def test_a_page_may_not_pass_200000_characters(monkeypatch):
    monkeypatch.setenv("REEF_FILE_TEXT_PAGE_CHARS", "200001")
    with pytest.raises(ValueError):
        config.Settings()
