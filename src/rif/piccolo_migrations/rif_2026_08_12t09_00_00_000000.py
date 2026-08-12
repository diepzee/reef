"""Route every RLS predicate through the bypassing helper functions."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import DB
from rif.rls import AUTHZ_ROLE, disable_statements, enable_statements

ID = "2026-08-12T09:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "authz helper functions; content predicates rewritten onto them"

_ROLE_MISSING = f"""{AUTHZ_ROLE} does not exist.

It owns the RLS helper functions and must hold BYPASSRLS, which only a
superuser can grant -- so it is created out of band rather than here.
Run scripts/provision_app_role.py against the admin credential first; it
creates the role, grants it to the migration role (required to assign
function ownership), and grants it CREATE on the schema.
"""


async def forwards() -> MigrationManager:
    """Install the helper functions and rebuild the content policies on them.

    Semantically a no-op: the new predicates ask exactly what the old ones
    asked, but reach ``memberships`` inside a ``SECURITY DEFINER`` function
    owned by a ``BYPASSRLS`` role instead of by subquerying it directly. That
    changes nothing today and is what makes it possible to put RLS on
    ``memberships`` in a later migration without every predicate recursing.

    Policies are dropped and recreated rather than altered, because Postgres
    has no ``ALTER POLICY ... USING`` that can swap a predicate atomically
    across four commands and four tables. The whole migration runs in one
    transaction, so a request either sees the old set or the new one.

    :raises RuntimeError: if the function-owner role has not been provisioned
    :returns: configured migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        role = await DB._run_in_new_connection(
            f"SELECT rolbypassrls FROM pg_roles WHERE rolname = '{AUTHZ_ROLE}'"
        )
        if not role:
            raise RuntimeError(_ROLE_MISSING)
        if not role[0]["rolbypassrls"]:
            raise RuntimeError(
                f"{AUTHZ_ROLE} exists without BYPASSRLS. Every policy calling its "
                f"functions would recurse until the stack is exhausted. "
                f"Re-run scripts/provision_app_role.py."
            )

        # disable_statements drops the old policies (including the legacy
        # FOR ALL names) and then the functions; enable_statements recreates
        # the functions first, then the policies that call them. Both are
        # idempotent, so a re-run after a partial failure is safe.
        for statement in disable_statements():
            await DB._run_in_new_connection(statement)
        for statement in enable_statements():
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
