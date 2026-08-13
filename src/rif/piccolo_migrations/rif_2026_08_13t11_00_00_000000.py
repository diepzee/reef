"""Give every person a session epoch, so a signed cookie can be revoked."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import run_ddl_atomically
from rif.rls import identity_statements

ID = "2026-08-13T11:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "persons.session_epoch and the lookup the request path uses"


async def forwards() -> MigrationManager:
    """Add the counter that ends a session, and the function that reads it.

    reef issues its own browser session as a signed cookie rather than a
    stored row, which is cheap and stateless and has one consequence: there
    is nothing to delete when a session has to stop working. Logging out
    cleared the cookie and left every copy of it valid, renewing itself on
    every request, for as long as somebody kept using it.

    ``session_epoch`` is what a sealed token is checked against instead.
    Every token carries the value it was sealed with; moving the column on
    invalidates all of them at once. Existing rows default to ``0``, which is
    what tokens sealed before this migration carry -- so the deploy signs
    nobody out.

    The column and the function go on together, in one transaction. Apart,
    there is a window where the request path calls a function that does not
    exist yet (every request 500s) or reads a column that does not exist yet
    (the same). Both orders are broken, so neither is used.

    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        await run_ddl_atomically(
            [
                (
                    "ALTER TABLE persons ADD COLUMN IF NOT EXISTS "
                    "session_epoch INTEGER NOT NULL DEFAULT 0"
                ),
                *identity_statements(),
            ]
        )

    manager.add_raw(run)
    return manager
