import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(env_prefix="RIF_")

    database_url: str = "postgresql+asyncpg://rif:rif@localhost:5433/rif"
    test_database_url: str = "postgresql+asyncpg://rif:rif@localhost:5433/rif_test"
    context_char_budget: int = 150_000
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    image_max_bytes: int = 5_000_000
    signed_url_ttl_seconds: int = 300

    @property
    def async_database_url(self) -> str:
        """Return the database URL with the asyncpg driver scheme.

        Railway injects ``DATABASE_URL`` as ``postgresql://``; SQLAlchemy's
        async engine needs ``postgresql+asyncpg://``.

        :returns: a URL usable by create_async_engine
        """
        url = os.environ.get("DATABASE_URL", self.database_url)
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    :returns: cached settings instance
    """
    return Settings()
