import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

#: What settings are spelled with now.
PREFIX = "REEF_"

#: What they were spelled with when the module was called reef. Kept working
#: because twelve of them are set on Railway production -- the signing key
#: and the open-door pair among them -- and a variable renamed in code but
#: not in the deployment does not read as "renamed", it reads as absent.
#: reef treats absent config as a reason to refuse to boot, correctly, so
#: the rename has to land in code before the environment, not with it.
LEGACY_PREFIX = "RIF_"


def legacy_environment_names() -> list[str]:
    """Return the ``RIF_``-prefixed variables still doing work, sorted.

    Empty means every setting arrives under the new name and the fallback
    below can be deleted. That is the evidence for removing it; without this
    the decision is a guess about what Railway holds.

    :returns: legacy variable names with no ``REEF_`` equivalent set
    """
    return sorted(
        name
        for name in os.environ
        if name.startswith(LEGACY_PREFIX)
        and f"{PREFIX}{name[len(LEGACY_PREFIX) :]}" not in os.environ
    )


def env(suffix: str) -> str | None:
    """Read one variable by suffix, preferring the new prefix.

    For the handful of values read straight from the environment rather than
    through :class:`Settings` -- the dev-mode escape hatches and the base
    URL, which are consulted before settings exist.

    :param suffix: the name without a prefix, for example ``BASE_URL``
    :returns: the value, or ``None`` when neither spelling is set
    """
    value = os.environ.get(f"{PREFIX}{suffix}")
    if value is not None:
        return value
    return os.environ.get(f"{LEGACY_PREFIX}{suffix}")


def adopt_legacy_environment() -> None:
    """Copy any legacy variable onto its new name, where the new one is unset.

    Done once, before :class:`Settings` reads the environment, because
    pydantic-settings takes a single prefix and the alternative is declaring
    an alias on all seventeen fields.
    """
    for name in legacy_environment_names():
        os.environ[f"{PREFIX}{name[len(LEGACY_PREFIX) :]}"] = os.environ[name]


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(env_prefix=PREFIX)

    database_url: str = "postgresql://reef:reef@localhost:5433/reef"
    test_database_url: str = "postgresql://reef:reef@localhost:5433/reef_test"
    migration_database_url: str = ""
    context_char_budget: int = 150_000
    # Above context_char_budget on purpose: a page bigger than the whole
    # context budget cannot be loaded in one piece anyway, so this refuses
    # only what was never usable memory. See reef.pages.validate_body.
    page_max_chars: int = 200_000
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    file_max_bytes: int = 25_000_000
    signed_url_ttl_seconds: int = 300
    session_secret: str = ""
    static_dir: str = "frontend/dist"
    site_dir: str = "site"
    # The launch exception. Both default to the closed position, and both
    # have to be set for the door to admit anybody -- see reef.opendoor for
    # why it fails closed on either one alone.
    open_seats: int = 0
    open_until: str = ""

    # Email on the Glama account that owns this deployment's listing, served
    # at /.well-known/glama.json to claim it. Deliberately unset by default:
    # it is a personal address, and a fork claiming ours would only publish
    # it on their domain to no effect. Unset means the route 404s, which is
    # the honest answer -- nobody has claimed this deployment.
    glama_maintainer_email: str = ""
    # --- MCP OAuth proxy (see docs/runbook.md, "reef as authorization
    # server"). All three are read only when WORKOS_MCP_CLIENT_ID/SECRET
    # select the proxy branch of reef.server._build_auth.
    # Signs reef-issued JWTs and keys the OAuth store's encryption.
    # Explicit, never derived from the WorkOS client secret: the default
    # derivation would make a secret rotation silently invalidate every
    # issued token AND orphan the store directory.
    jwt_signing_key: str = ""
    # Where OAuth state lives; production points at the Railway volume.
    oauth_store_dir: str = ""
    # Comma-separated redirect-URI patterns MCP clients may register.
    # Empty means the default allowlist in reef.server.
    allowed_client_redirects: str = ""

    @property
    def dsn(self) -> str:
        """Return the connection DSN, preferring Railway's injected value.

        Railway injects ``DATABASE_URL`` as ``postgresql://``, which is what
        asyncpg -- and therefore Piccolo -- wants unmodified. The SQLAlchemy
        original had to rewrite the scheme here; Piccolo does not.

        :returns: a DSN usable by asyncpg
        """
        return os.environ.get("DATABASE_URL", self.database_url)

    @property
    def migration_dsn(self) -> str:
        """Return the DSN schema migrations should run under.

        The app's own role must not be able to run DDL: it is the
        RLS-constrained principal, and a role that can ``ALTER TABLE`` can
        also ``DROP POLICY``. Migrations therefore need a separate, more
        privileged credential, supplied as ``REEF_MIGRATION_DATABASE_URL``.

        Falls back to :attr:`dsn` when unset, which keeps local development
        and the test suite working unchanged -- there the app role owns its
        own database and no split is needed.

        :returns: a DSN usable by asyncpg for DDL
        """
        return self.migration_database_url or self.dsn


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    :returns: cached settings instance
    """
    adopt_legacy_environment()
    return Settings()
