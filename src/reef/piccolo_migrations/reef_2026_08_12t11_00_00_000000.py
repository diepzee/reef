"""Narrow the pre-auth identity lookups to definer functions."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB
from reef.rls import identity_statements

ID = "2026-08-12T11:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "identity-binding helper functions"


async def forwards() -> MigrationManager:
    """Install the four functions identity binding resolves through.

    No policy changes and no behaviour change: the application asks the same
    questions it always asked, through functions that can only answer them by
    exact key. What it buys is that the pre-auth path -- the one place that
    must read ``persons`` with no principal armed -- loses the ability to run
    an arbitrary ``WHERE`` over the table, before ``persons`` gains a policy
    in the next phase.

    Idempotent throughout (``CREATE OR REPLACE`` plus guarded grants), so a
    re-run after a partial failure is safe. The functions may already exist if
    the previous phase's migration ran after this code landed, since both call
    into ``reef.rls``; installing them twice is a no-op.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        for statement in identity_statements():
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
