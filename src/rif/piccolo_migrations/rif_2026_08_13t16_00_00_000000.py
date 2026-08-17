"""Open the door for the launch, countably and reversibly."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import run_ddl_atomically
from rif.rls import open_door_statements, person_column_grant_statements

ID = "2026-08-13T16:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "persons.joined_open_door, the admission function, column grants"


async def forwards() -> MigrationManager:
    """Add the launch exception's one column and its admission function.

    reef is invitation-only and stays that way; this migration only makes it
    *possible* to open the door, and leaves it shut. Both settings the door
    reads (``RIF_OPEN_SEATS``, ``RIF_OPEN_UNTIL``) default to the closed
    position, so a deployment that applies this and changes nothing else
    behaves exactly as it did before. See :mod:`rif.opendoor`.

    No backfill, and deliberately so. ``DEFAULT FALSE`` on the ``ADD COLUMN``
    is what fills existing rows, which Postgres does from catalog metadata
    without rewriting the table and without row security being involved at
    all. An ``UPDATE`` here would have needed the ``FORCE ROW LEVEL
    SECURITY`` dance the alias migration documents -- and would have silently
    matched nothing without it. The column that needs no backfill is the one
    that cannot get that wrong.

    ``NOT NULL`` because the seat count is ``count(*) WHERE
    joined_open_door``, and a NULL there is neither counted nor visibly
    uncounted -- exactly the kind of third state a ceiling should not have.

    The column grants are the second half. ``persons_self_update`` admits a
    person's whole row, so without narrowing, any member could set their own
    ``joined_open_door`` and spend launch seats that were never theirs. That
    revoke lands in the same transaction as the column, so there is no window
    in which the flag exists and is writable.

    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        await run_ddl_atomically(
            [
                (
                    "ALTER TABLE persons ADD COLUMN IF NOT EXISTS joined_open_door "
                    "BOOLEAN NOT NULL DEFAULT FALSE"
                ),
                *open_door_statements(),
                *person_column_grant_statements(),
            ]
        )

    manager.add_raw(run)
    return manager
