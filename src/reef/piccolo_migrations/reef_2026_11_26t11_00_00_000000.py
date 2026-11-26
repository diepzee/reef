"""Give the helper functions a safe search path.

Changes one setting on Reef's own helper functions, the ones that run with
their owner's rights: ``search_path`` becomes ``pg_catalog, public, pg_temp``.
It was ``public, pg_catalog``. That let an object in ``public`` with the same
name and argument types stand in for a built-in, and it left temporary tables
to be searched first.

In an existing database this alters every function in ``public`` that runs
with its owner's rights and whose name starts with ``reef_``, whatever its
arguments. That is every helper Reef has made, also one left behind under a
name that is no longer used. It changes no function's body, owner or rights,
and no table, row, index or policy. Functions that belong to extensions do
not have that prefix and are not touched. No role is named, so a database
whose roles still have the old names works unchanged. The statement is fixed
text, never imported from ``reef.rls``, runs in one transaction, and is safe
to run twice.

A helper added after this migration gets its setting from the fixed text of
the migration that creates it. ``tests/test_migration_chain.py`` fails when
the whole chain leaves any helper with another setting.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-26T11:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "helper functions look in the system catalog first"

STATEMENTS = [
    r"""DO $helpers$
DECLARE
    helper regprocedure;
BEGIN
    FOR helper IN
        SELECT p.oid::regprocedure
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prosecdef
          AND p.proname LIKE 'reef\_%'
    LOOP
        EXECUTE format(
            'ALTER FUNCTION %s SET search_path = pg_catalog, public, pg_temp',
            helper
        );
    END LOOP;
END
$helpers$"""
]


async def forwards() -> MigrationManager:
    """Set the search path on every helper that exists.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Run the fixed statement atomically."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
