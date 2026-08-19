"""Put the identity tables under row-level security."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.rls import (
    drop_identity_policy_statements,
    identity_grant_statements,
    identity_policy_statements,
    mutation_statements,
)

ID = "2026-08-12T16:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "identity policies, mutation functions, spaces column grant"


async def forwards() -> MigrationManager:
    """Enforce isolation of ``persons``, ``spaces`` and ``memberships``.

    The one migration in this sequence that changes what a request can see.
    Everything before it was groundwork: the bypassing helper functions the
    predicates call, the narrowed identity lookups, and the disclosure
    functions the application already reads through. By now no application
    code reaches another person's row directly, so switching the policies on
    should change no behaviour -- only what would happen if some future code
    forgot a filter.

    Functions first, policies second: ``memberships_insert`` calls
    ``reef_owns_space``, and ``CREATE POLICY`` resolves the name immediately.

    The column grant is not optional decoration. ``spaces_member_update``
    has to admit every member because a page write bumps ``spaces.version``,
    and row security cannot say *which column* -- so without the grant a
    member could rename a cove or take its ownership with one statement.

    All of it runs on one connection inside one transaction, so a request
    sees either the old shape or the new one. That is not the default:
    Piccolo's ``_run_in_new_connection`` commits each statement on its own
    connection, and a failure between "enable row security" and "create the
    policies" would leave a table denying every row to everyone.

    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        await run_ddl_atomically(
            drop_identity_policy_statements()
            + mutation_statements()
            + identity_policy_statements()
            + identity_grant_statements()
        )

    manager.add_raw(run)
    return manager
