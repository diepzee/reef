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
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    image_max_bytes: int = 5_000_000
    signed_url_ttl_seconds: int = 300
    session_secret: str = ""
    static_dir: str = "frontend/dist"
    site_dir: str = "site"

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
