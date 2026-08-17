import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(env_prefix="RIF_")

    database_url: str = "postgresql://rif:rif@localhost:5433/rif"
    test_database_url: str = "postgresql://rif:rif@localhost:5433/rif_test"
    migration_database_url: str = ""
    context_char_budget: int = 150_000
    # Above context_char_budget on purpose: a page bigger than the whole
    # context budget cannot be loaded in one piece anyway, so this refuses
    # only what was never usable memory. See rif.pages.validate_body.
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
    # have to be set for the door to admit anybody -- see rif.opendoor for
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
    # select the proxy branch of rif.server._build_auth.
    # Signs reef-issued JWTs and keys the OAuth store's encryption.
    # Explicit, never derived from the WorkOS client secret: the default
    # derivation would make a secret rotation silently invalidate every
    # issued token AND orphan the store directory.
    jwt_signing_key: str = ""
    # Where OAuth state lives; production points at the Railway volume.
    oauth_store_dir: str = ""
    # Comma-separated redirect-URI patterns MCP clients may register.
    # Empty means the default allowlist in rif.server.
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
        privileged credential, supplied as ``RIF_MIGRATION_DATABASE_URL``.

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
    return Settings()
