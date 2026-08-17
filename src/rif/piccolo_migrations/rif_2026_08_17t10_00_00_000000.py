"""Remember which release each person has already read about."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import run_ddl_atomically

ID = "2026-08-17T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "persons.last_seen_release, for the what's-new marker"


async def forwards() -> MigrationManager:
    """Add the per-person read marker for the what's-new panel.

    A plain column on an existing table, deliberately: ``persons`` is
    already self-only under ``persons_self_select`` and
    ``persons_self_update``, so the new value is covered by policies that
    predate it. Nothing is added to :func:`rif.rls.enable_statements` --
    a new table would have needed policy DDL there, and putting policy DDL
    for a *new* table into that function is what broke fresh builds twice.

    ``IF NOT EXISTS`` so a database that already ran this by hand, or a
    re-run of a partially applied chain, is not an error.

    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        await run_ddl_atomically(
            ["ALTER TABLE persons ADD COLUMN IF NOT EXISTS last_seen_release VARCHAR"]
        )

    manager.add_raw(run)
    return manager
