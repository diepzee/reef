"""Store each row's search vector."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.search import search_statements

ID = "2026-11-17T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "stored English and Dutch search vectors"


async def forwards() -> MigrationManager:
    """Add a generated ``search_vector`` column to pages and attachments.

    Search used to parse every visible body on every query. The vector is
    now computed once, on write, by a generated column. No index is added:
    under row security Postgres cannot use one for a text match -- see
    ``reef.search``.

    Adding a stored generated column rewrites the table under an exclusive
    lock, for as long as it takes to parse every existing row. That is the
    price of there being no backfill to run or to forget: when this returns,
    every row already has its vector.

    No policy changes. A generated column is covered by the table's existing
    row security and by its existing table-level grants.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically(search_statements())

    manager.add_raw(run)
    return manager
