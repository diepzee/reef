"""Store what the index shows for a page, beside the page."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-17T11:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "pages carry their index description, size and reference targets"

ADD_COLUMNS = """
ALTER TABLE pages
    ADD COLUMN IF NOT EXISTS description VARCHAR,
    ADD COLUMN IF NOT EXISTS size INTEGER,
    ADD COLUMN IF NOT EXISTS reference_targets VARCHAR[],
    ADD COLUMN IF NOT EXISTS indexed_version INTEGER
"""


async def forwards() -> MigrationManager:
    """Add the stored index fields to ``pages``, empty.

    Building the index used to read every accessible body to derive a
    description, a size and the wiki-link targets. Those are now stored when
    a page is saved.

    There is deliberately **no backfill here**. The migration role is subject
    to row security like every other role, and with no person armed it can
    see no pages at all -- a backfill run from here would update nothing and
    report success. Existing rows stay empty and are recognised as such:
    ``indexed_version`` is null, which never equals ``version``, so the index
    goes on deriving their values from the body exactly as before.
    ``reef.context.backfill_index_fields`` fills them in, acting as a person.

    Nullable columns with no default, so adding them rewrites nothing. No
    policy changes: the existing row security and table-level grants on
    ``pages`` cover new columns.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically([ADD_COLUMNS])

    manager.add_raw(run)
    return manager
