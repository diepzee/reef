"""Put the identity tables under row-level security."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import DB
from rif.rls import (
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
    ``rif_owns_space``, and ``CREATE POLICY`` resolves the name immediately.

    The column grant is not optional decoration. ``spaces_member_update``
    has to admit every member because a page write bumps ``spaces.version``,
    and row security cannot say *which column* -- so without the grant a
    member could rename a cove or take its ownership with one statement.

    Policies are dropped first so a re-run after a partial failure is safe.
    The whole migration runs in one transaction, so a request sees either
    the old shape or the new one.

    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        for statement in drop_identity_policy_statements():
            await DB._run_in_new_connection(statement)
        for statement in mutation_statements():
            await DB._run_in_new_connection(statement)
        for statement in identity_policy_statements():
            await DB._run_in_new_connection(statement)
        for statement in identity_grant_statements():
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
