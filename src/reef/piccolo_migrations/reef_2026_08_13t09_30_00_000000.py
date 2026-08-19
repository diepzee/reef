"""Give a person a face: an avatar stored on their own row."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB

ID = "2026-08-13T09:30:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "person avatars"


async def forwards() -> MigrationManager:
    """Add nullable avatar columns to ``persons``.

    Nullable rather than defaulted: "has not chosen a picture" is a real
    state the UI renders differently (initials), not an empty image. Existing
    rows therefore need no backfill.

    No grant or policy change accompanies this. ``persons_self_select`` and
    ``persons_self_update`` already scope a person to their own row, and
    ``persons`` carries no column-level revoke, so the new columns inherit
    exactly the access the rest of the row has.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await DB._run_in_new_connection(
            "ALTER TABLE persons ADD COLUMN avatar_mime VARCHAR(255) DEFAULT NULL"
        )
        await DB._run_in_new_connection(
            "ALTER TABLE persons ADD COLUMN avatar_bytes BYTEA DEFAULT NULL"
        )

    manager.add_raw(run)
    return manager
