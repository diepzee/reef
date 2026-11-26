"""Index the latest jobs of one kind for one person.

Adds the non-unique B-tree index ``jobs_person_kind_latest`` on
``jobs (person_id, kind, created_at DESC, id DESC)`` in an existing database.
The id completes the reading query's order when creation times tie.

Changes no rights: table and function ownership, grants, row security and
policies are left alone, as are stored rows and all existing indexes.
No role names are referenced, so databases still using ``rif_app`` and
``rif_authz`` work without changes.

The jobs table is small in production, so a plain ``CREATE INDEX`` inside
the migration's transaction is acceptable. The SQL is fixed text, never
imported from ``reef.rls``, and ``IF NOT EXISTS`` makes it safe to run twice.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-26T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "index the latest jobs of one kind for one person"

STATEMENTS = [
    (
        "CREATE INDEX IF NOT EXISTS jobs_person_kind_latest "
        "ON jobs (person_id, kind, created_at DESC, id DESC)"
    )
]


async def forwards() -> MigrationManager:
    """Add the index in one transaction.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Run the fixed index statement atomically."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
