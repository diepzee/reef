"""Allow corrections as a distinct kind of engram use.

Replaces the ``engram_access_via_known`` check constraint on
``engram_access`` in an existing database. It accepts ``correct`` alongside
``read``, ``write``, ``remember`` and ``web``; other kinds remain refused.

Changes no rights: table and function ownership, grants, row security and
policies stay as they are. Stored rows, weights, indexes and every other
constraint are left alone. Earlier corrections remain routine writes because
the usage log did not distinguish them when they were recorded.

No role is named, so databases still using ``rif_app`` and ``rif_authz``
work unchanged. The SQL is fixed text, never imported from ``reef.rls``,
runs in one transaction, and is safe to run twice.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-26T12:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "allow corrections as a distinct kind of engram use"

STATEMENTS = [
    "ALTER TABLE engram_access DROP CONSTRAINT IF EXISTS engram_access_via_known",
    (
        "ALTER TABLE engram_access ADD CONSTRAINT engram_access_via_known "
        "CHECK (via IN ('read', 'write', 'remember', 'web', 'correct'))"
    ),
]


async def forwards() -> MigrationManager:
    """Allow the correction kind in one transaction.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Replace the constraint atomically with fixed SQL."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
