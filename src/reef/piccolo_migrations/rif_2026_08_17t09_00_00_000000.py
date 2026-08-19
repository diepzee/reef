"""Let an invitation grant reading without writing."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.rls import mutation_statements

ID = "2026-08-17T09:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "rif_admit_member gains a role parameter"


async def forwards() -> MigrationManager:
    """Replace ``rif_admit_member`` with the four-argument version.

    The write policies have required ``role = 'member'`` since day one; what
    was missing was any way to *create* a viewer on purpose. The admit
    function now takes the role, validates it in the same place the row is
    written, and refuses anything but ``member`` or ``viewer``.

    ``CREATE OR REPLACE`` cannot change a signature, so
    :func:`reef.rls.mutation_statements` now begins by dropping the
    three-argument form — a no-op on a fresh chain, where the historical
    migration that first runs the group already creates the four-argument
    version. Re-running the whole group keeps every deployment on one
    definition rather than maintaining two.
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        await run_ddl_atomically([*mutation_statements()])

    manager.add_raw(run)
    return manager
