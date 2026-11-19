"""Tag every existing log with ``log``.

The ``log`` method and ``brief`` know a log by its tag, and nothing tagged
logs before them. By convention a log was named ``<topic>-log.md``, such as
``climbing-log.md``, so each engram whose path ends in ``-log.md`` gets the
tag, if it does not have it yet. Later parts, ``-log-2.md`` and on, are
written by ``log`` itself, tagged.

Only the tags change: no revision is written, no version moves and
``updated_at`` stays, so history and "what changed" do not show a change
nobody made. Nothing stored is derived from tags -- the search vector reads
title and body, the index fields and the embeddings read the body -- so
nothing goes stale. Running it again changes nothing.

The SQL is fixed text, never imported from a live module: a migration must
run the same SQL on the day it is applied as on the day it was written.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-20T14:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "tag engrams named *-log.md with log"

#: Run in order, in one transaction.
STATEMENTS = [
    # The update is a bulk statement with no principal armed, and engrams
    # carries FORCE ROW LEVEL SECURITY, which subjects the table's owner to
    # the policies too: left on, the update matches no rows at all. So it is
    # lifted for this transaction only, as the alias backfill of 13 August
    # 2026 does, and forced again below. The ALTER takes an ACCESS EXCLUSIVE
    # lock held to the end of the transaction, so no request ever sees the
    # table with its policies relaxed.
    "ALTER TABLE engrams NO FORCE ROW LEVEL SECURITY",
    (
        "UPDATE engrams SET tags = array_append(tags, 'log') "
        "WHERE path LIKE '%-log.md' AND NOT ('log' = ANY(tags))"
    ),
    "ALTER TABLE engrams FORCE ROW LEVEL SECURITY",
]


async def forwards() -> MigrationManager:
    """Tag each engram named ``*-log.md`` with ``log``, under lifted row security.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Run :data:`STATEMENTS` atomically."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
