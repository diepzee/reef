"""Preserve original filenames now attachments are general file storage."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB

ID = "2026-08-11T20:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "attachment filenames"


async def forwards() -> MigrationManager:
    """Add a filename column without disturbing existing object keys.

    Existing image rows keep an empty filename and fall back to their opaque
    key in API and export representations.

    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        await DB._run_in_new_connection(
            "ALTER TABLE attachments ADD COLUMN filename VARCHAR(512) DEFAULT '' NOT NULL"
        )

    manager.add_raw(run)
    return manager
