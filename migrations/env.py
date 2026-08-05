import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, async_engine_from_config

from rif.config import get_settings
from rif.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The database URL always comes from Settings, never the .ini file, so
# local/test/production all resolve the same way the app itself does.
config.set_main_option("sqlalchemy.url", get_settings().async_database_url)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

# Arbitrary constant identifying this project's migration lock; any two
# processes racing to migrate the same database serialize on it.
_MIGRATION_LOCK_ID = 715001


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the migration scripts against an already-open connection.

    :param connection: a live, synchronous-facing database connection
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await _run_migrations_under_lock(connection)

    await connectable.dispose()


async def _run_migrations_under_lock(connection: AsyncConnection) -> None:
    """Hold a Postgres advisory lock for the duration of the migration run.

    Concurrent container starts each try to migrate on boot; without this,
    two of them running DDL against the same database at once can race.
    The lock itself is session-scoped, not transactional, so it survives
    the commit below; but that commit still matters; without it the
    lock-acquiring statement leaves the connection inside an open
    transaction, which alembic detects as an *externally managed*
    transaction and then never commits its own DDL, silently discarding
    the whole migration.

    :param connection: the async connection migrations will run on
    """
    await connection.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": _MIGRATION_LOCK_ID})
    await connection.commit()
    try:
        await connection.run_sync(do_run_migrations)
    finally:
        await connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": _MIGRATION_LOCK_ID})
        await connection.commit()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
