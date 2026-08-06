"""Apply the composite constraints and the RLS policies.

Piccolo's auto-generated migration builds the tables; it cannot know about
either the multi-column constraints its table syntax can't express or the
row-level security that is this project's actual privacy boundary. Both come
from ``rif.rls``, the same module ``tests/conftest.py`` applies, so the
policies under test and the policies in production cannot drift apart.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import DB
from rif.rls import constraint_statements, enable_statements

ID = "2026-08-06T12:20:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "composite constraints and row-level security policies"


async def forwards() -> MigrationManager:
    """Add the constraints and turn on RLS.

    :returns: the migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        """Execute the DDL, in order."""
        for statement in constraint_statements() + enable_statements():
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
