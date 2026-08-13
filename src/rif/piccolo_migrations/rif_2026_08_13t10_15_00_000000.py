"""Let each person choose how a cove looks to them, and to nobody else."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import DB
from rif.rls import appearance_statements

ID = "2026-08-13T10:15:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "per-person cove appearance"

_EXECUTOR_ROLES = ("rif_app", "rif", "rif_probe")


async def forwards() -> MigrationManager:
    """Create ``space_appearances`` with its policy and grants.

    Both foreign keys cascade. A deleted cove takes every viewer's private
    preference for it with it, and a deleted person takes all of theirs --
    neither is worth keeping, and an orphan row here is unreachable under
    the policy anyway.

    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        await DB._run_in_new_connection(
            "CREATE TABLE IF NOT EXISTS space_appearances ("
            "  id UUID PRIMARY KEY,"
            "  person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,"
            "  space_id UUID NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,"
            "  color VARCHAR(255) DEFAULT NULL,"
            "  glyph VARCHAR(255) DEFAULT NULL"
            ")"
        )
        # One row per person per cove: the API upserts against this pair, and
        # without the constraint a retried write would quietly stack rows.
        await DB._run_in_new_connection(
            "CREATE UNIQUE INDEX IF NOT EXISTS space_appearances_person_space "
            "ON space_appearances (person_id, space_id)"
        )
        for role in _EXECUTOR_ROLES:
            await DB._run_in_new_connection(
                f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
                f"'{role}') THEN EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE "
                f"ON space_appearances TO {role}'; END IF; END $$"
            )
        for statement in appearance_statements():
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
