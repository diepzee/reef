"""Give every page the time it last changed, not the time it was made.

The Piccolo port of 6 August dropped the SQLAlchemy model's
``onupdate=func.now()`` on ``pages.updated_at``, and nothing replaced it, so
the column kept its creation value through every edit. The application now
sets it on each write. This migration repairs the pages written meanwhile:
each page's ``updated_at`` becomes the time of its newest revision, when
that is later than what is stored. A page never edited keeps its value.

Both tables carry ``FORCE ROW LEVEL SECURITY``, which binds their owner too,
and a migration arms no person. Unlifted, the ``UPDATE`` would match no row
and report success. So, as the August alias backfill does, enforcement is
lifted inside the transaction and restored before it commits. The statements
below are fixed text, not imported from :mod:`reef.rls`; the policies are not
touched, only switched off and on again.

Running it twice changes nothing: the second run finds no revision later
than the stored value.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-20T09:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "backfill pages.updated_at from the newest revision"

STATEMENTS = [
    "ALTER TABLE pages NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE revisions NO FORCE ROW LEVEL SECURITY",
    (
        "UPDATE pages p SET updated_at = r.newest "
        "FROM (SELECT page_id, max(created_at) AS newest "
        "FROM revisions GROUP BY page_id) r "
        "WHERE r.page_id = p.id AND r.newest > p.updated_at"
    ),
    "ALTER TABLE revisions FORCE ROW LEVEL SECURITY",
    "ALTER TABLE pages FORCE ROW LEVEL SECURITY",
]


async def forwards() -> MigrationManager:
    """Set each page's ``updated_at`` to its newest revision's time.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
