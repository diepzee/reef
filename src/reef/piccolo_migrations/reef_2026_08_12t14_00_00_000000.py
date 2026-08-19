"""Move roster and invite disclosure behind definer functions."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB
from reef.rls import disclosure_statements

ID = "2026-08-12T14:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "roster, owner, display-name and invite-budget functions"


async def forwards() -> MigrationManager:
    """Install the functions that decide who may see whose details.

    Still no policies, so nothing a user can see changes. What changes is
    where the rule lives: "only a cove's owner sees member email addresses"
    moves out of a web handler that had already fetched the addresses and
    remembered to blank them, and into ``reef_roster``, which never returns
    them to anyone else and returns nothing at all to a non-member.

    Idempotent, so a re-run after a partial failure is safe.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        for statement in disclosure_statements():
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
