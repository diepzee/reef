"""Make account deletion preserve other people's shared data safely."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB

ID = "2026-08-11T20:10:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "safe account deletion foreign keys"

STATEMENTS = [
    (
        "ALTER TABLE persons DROP CONSTRAINT persons_invited_by_person_id_fkey, "
        "ADD CONSTRAINT persons_invited_by_person_id_fkey "
        "FOREIGN KEY (invited_by_person_id) REFERENCES persons(id) "
        "ON UPDATE CASCADE ON DELETE SET NULL"
    ),
    "ALTER TABLE revisions ALTER COLUMN author_id DROP NOT NULL",
    (
        "ALTER TABLE revisions DROP CONSTRAINT revisions_author_id_fkey, "
        "ADD CONSTRAINT revisions_author_id_fkey "
        "FOREIGN KEY (author_id) REFERENCES persons(id) "
        "ON UPDATE CASCADE ON DELETE SET NULL"
    ),
    (
        "ALTER TABLE promotions DROP CONSTRAINT promotions_dest_cove_id_fkey, "
        "ADD CONSTRAINT promotions_dest_cove_id_fkey "
        "FOREIGN KEY (dest_cove_id) REFERENCES coves(id) "
        "ON UPDATE CASCADE ON DELETE CASCADE"
    ),
]


async def forwards() -> MigrationManager:
    """Replace cascading identity references with preservation-safe actions."""
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        for statement in STATEMENTS:
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
