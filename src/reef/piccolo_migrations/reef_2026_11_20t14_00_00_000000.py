"""Tag every existing log with ``log``.

The ``log`` method and ``brief`` know a log by its tag, and nothing tagged
logs before them. By convention a log was named ``<topic>-log.md``, such as
``climbing-log.md``, so each engram whose path ends in ``-log.md`` gets the
tag, if it does not have it yet. Later parts, ``-log-2.md`` and on, are
written by ``log`` itself, tagged.

On the engrams only the tags change: no revision is written, no engram
version moves and ``updated_at`` stays, so history and "what changed" do not
show a change nobody made. Nothing stored is derived from tags -- the search
vector reads title and body, the index fields and the embeddings read the
body -- so nothing goes stale. Each space with a newly tagged engram does get
its version bumped, once, so a client that caches the index by that version
loads it again and sees the tags. Running it again tags nothing and bumps
nothing.

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
    # and spaces both carry FORCE ROW LEVEL SECURITY, which subjects the
    # tables' owner to the policies too: left on, the update matches no rows
    # at all. So it is lifted for this transaction only, as the alias
    # backfill of 13 August 2026 does, and forced again below. Each ALTER
    # takes an ACCESS EXCLUSIVE lock held to the end of the transaction, so
    # no request ever sees a table with its policies relaxed.
    "ALTER TABLE engrams NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE spaces NO FORCE ROW LEVEL SECURITY",
    # One statement: the spaces bumped are exactly those of the engrams this
    # run tagged, each once, however many of its engrams were tagged.
    (
        "WITH tagged AS ("
        "UPDATE engrams SET tags = array_append(tags, 'log') "
        "WHERE path LIKE '%-log.md' AND NOT ('log' = ANY(tags)) "
        "RETURNING space_id) "
        "UPDATE spaces SET version = version + 1 "
        "WHERE id IN (SELECT space_id FROM tagged)"
    ),
    "ALTER TABLE spaces FORCE ROW LEVEL SECURITY",
    "ALTER TABLE engrams FORCE ROW LEVEL SECURITY",
]


async def forwards() -> MigrationManager:
    """Tag each engram named ``*-log.md`` with ``log`` and bump its space's version.

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
