"""Route every RLS predicate through the bypassing helper functions."""

import asyncpg
from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB
from reef.rls import AUTHZ_ROLE, FORMER_AUTHZ_ROLE, create_authz_role_statements

ID = "2026-08-12T09:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "authz helper functions; content predicates rewritten onto them"


# --- frozen snapshot -------------------------------------------------------
#
# These lists held `disable_statements()` and `enable_statements()` imported
# live from `reef.rls`. That import is what made this migration a moving
# target: it runs against the schema of its own day, while the functions it
# called kept growing new columns. `session_epoch` and `memberships.alias`
# both landed inside `enable_statements` and broke every build from scratch,
# silently, because production was already past this point.
#
# Frozen here as the DDL actually stood on the day, following
# `2026-08-06T12:20:00`. History is inert; the newest migration is the one
# that re-applies today's policies, so drift has exactly one place to live.


_FROZEN_DISABLE = [
    "DROP POLICY IF EXISTS promotions_owner ON promotions",
    "ALTER TABLE promotions NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE promotions DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS revisions_select ON revisions",
    "DROP POLICY IF EXISTS revisions_insert ON revisions",
    "DROP POLICY IF EXISTS revisions_update ON revisions",
    "DROP POLICY IF EXISTS revisions_delete ON revisions",
    "DROP POLICY IF EXISTS revisions_member ON revisions",
    "ALTER TABLE revisions NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE revisions DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS attachments_select ON attachments",
    "DROP POLICY IF EXISTS attachments_insert ON attachments",
    "DROP POLICY IF EXISTS attachments_update ON attachments",
    "DROP POLICY IF EXISTS attachments_delete ON attachments",
    "DROP POLICY IF EXISTS attachments_member ON attachments",
    "ALTER TABLE attachments NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE attachments DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS pages_select ON pages",
    "DROP POLICY IF EXISTS pages_insert ON pages",
    "DROP POLICY IF EXISTS pages_update ON pages",
    "DROP POLICY IF EXISTS pages_delete ON pages",
    "DROP POLICY IF EXISTS pages_member ON pages",
    "ALTER TABLE pages NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE pages DISABLE ROW LEVEL SECURITY",
    "DROP FUNCTION IF EXISTS reef_space_ids()",
    "DROP FUNCTION IF EXISTS reef_member_space_ids()",
]

# The owner named here moved with the role itself. These statements are
# frozen against code drift, not against the cluster: any database that
# already ran this migration will never run it again, and every database
# that runs it from now on is built by an initdb that creates reef_authz.
# Leaving the old name would fail on exactly the clusters this still runs
# against, and help none of the ones it does not.
_FROZEN_ENABLE = [
    "CREATE OR REPLACE FUNCTION reef_space_ids() RETURNS SETOF uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT space_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid$reef$",
    "ALTER FUNCTION reef_space_ids() OWNER TO reef_authz",
    "REVOKE ALL ON FUNCTION reef_space_ids() FROM PUBLIC",
    "GRANT SELECT ON memberships TO reef_authz",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_ids() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_ids() TO rif'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_member_space_ids() RETURNS SETOF uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT space_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'$reef$",
    "ALTER FUNCTION reef_member_space_ids() OWNER TO reef_authz",
    "REVOKE ALL ON FUNCTION reef_member_space_ids() FROM PUBLIC",
    "GRANT SELECT ON memberships TO reef_authz",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_space_ids() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_space_ids() TO rif'; END IF; END $do$",
    "ALTER TABLE pages ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE pages FORCE ROW LEVEL SECURITY",
    "CREATE POLICY pages_select ON pages FOR SELECT USING (space_id IN (SELECT reef_space_ids()))",
    "CREATE POLICY pages_insert ON pages FOR INSERT WITH CHECK (space_id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY pages_update ON pages FOR UPDATE USING (space_id IN (SELECT reef_member_space_ids())) WITH CHECK (space_id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY pages_delete ON pages FOR DELETE USING (space_id IN (SELECT reef_member_space_ids()))",
    "ALTER TABLE attachments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE attachments FORCE ROW LEVEL SECURITY",
    "CREATE POLICY attachments_select ON attachments FOR SELECT USING (space_id IN (SELECT reef_space_ids()))",
    "CREATE POLICY attachments_insert ON attachments FOR INSERT WITH CHECK (space_id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY attachments_update ON attachments FOR UPDATE USING (space_id IN (SELECT reef_member_space_ids())) WITH CHECK (space_id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY attachments_delete ON attachments FOR DELETE USING (space_id IN (SELECT reef_member_space_ids()))",
    "ALTER TABLE revisions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE revisions FORCE ROW LEVEL SECURITY",
    "CREATE POLICY revisions_select ON revisions FOR SELECT USING (page_id IN (SELECT id FROM pages WHERE space_id IN (SELECT reef_space_ids())))",
    "CREATE POLICY revisions_insert ON revisions FOR INSERT WITH CHECK (page_id IN (SELECT id FROM pages WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE POLICY revisions_update ON revisions FOR UPDATE USING (page_id IN (SELECT id FROM pages WHERE space_id IN (SELECT reef_member_space_ids()))) WITH CHECK (page_id IN (SELECT id FROM pages WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE POLICY revisions_delete ON revisions FOR DELETE USING (page_id IN (SELECT id FROM pages WHERE space_id IN (SELECT reef_member_space_ids())))",
    "ALTER TABLE promotions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE promotions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS promotions_owner ON promotions",
    "CREATE POLICY promotions_owner ON promotions USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
]

_ROLE_MISSING = f"""{AUTHZ_ROLE} does not exist and this connection may not create it.

It owns the RLS helper functions and must hold BYPASSRLS, which only a
superuser can grant. This migration creates it automatically when the
migration credential is privileged enough; yours is not, so an operator has
to do it once, out of band:

    railway run --service rif-app -- sh -c \\
      'psql "$REEF_MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 \\
         -f scripts/provision_authz_role.sql'

Then redeploy. Nothing was changed by this migration.
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
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        role = await DB._run_in_new_connection(
            f"SELECT rolbypassrls FROM pg_roles WHERE rolname IN "
            f"('{AUTHZ_ROLE}', '{FORMER_AUTHZ_ROLE}')"
        )
        if not role:
            # Try to create it. Whether this is allowed depends on the
            # migration credential: on Railway it is the cluster's bootstrap
            # superuser, so this succeeds and the deploy is self-contained.
            # A properly least-privileged migration role cannot create a
            # BYPASSRLS role, and there the operator does it out of band --
            # so a refusal is reported as instructions, not as a stack trace.
            try:
                for statement in create_authz_role_statements():
                    await DB._run_in_new_connection(statement)
            except asyncpg.exceptions.InsufficientPrivilegeError:
                raise RuntimeError(_ROLE_MISSING) from None
            role = await DB._run_in_new_connection(
                f"SELECT rolbypassrls FROM pg_roles WHERE rolname IN "
                f"('{AUTHZ_ROLE}', '{FORMER_AUTHZ_ROLE}')"
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
        for statement in _FROZEN_DISABLE:
            await DB._run_in_new_connection(statement)
        for statement in _FROZEN_ENABLE:
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
