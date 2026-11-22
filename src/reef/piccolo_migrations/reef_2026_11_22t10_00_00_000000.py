"""Flag made-up accounts for scripted conversations, and leave them out of the totals.

Creates ``test_accounts``: one row per person the operator has flagged, which
a person may read for themselves and nothing more. No policy lets anyone add,
change or remove a row, so only the operator, past row security, flags anyone.

Adds ``reef_test_account_reset()``, which refuses a caller who shares a space
with anyone and otherwise puts a flagged caller's own account back to a fixed
state (their queued jobs and their embedding and reading spend are deleted)
and returns when, and ``reef_test_account_hurry()``, which makes a flagged
caller's queued jobs due now. Both act for the armed person only and are owned
by the authz role.

Replaces ``reef_call_log_totals`` so that it skips flagged accounts.

The SQL is fixed text: :data:`STATEMENTS` and :data:`TOTALS` were printed from
``reef.rls.test_account_statements()`` and ``reef.rls.call_log_statements()`` on
the day this was written. This module never imports ``reef.rls``: a migration
must run the same SQL on the day it is applied as on the day it was written.
``tests/test_migration_chain.py`` checks that what the chain builds is what
``reef.rls`` declares.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-22T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "flag test accounts and leave them out of the call totals"

#: As Piccolo creates it from ``reef.models``.
CREATE_TABLE = (
    'CREATE TABLE IF NOT EXISTS "test_accounts" ("person_id" UUID PRIMARY KEY '
    'REFERENCES "persons" ("id") ON DELETE CASCADE ON UPDATE CASCADE DEFAULT null, '
    '"flagged_at" TIMESTAMPTZ NOT NULL DEFAULT current_timestamp, '
    '"reset_at" TIMESTAMPTZ DEFAULT null)'
)

#: Run in order, after the table. Each can run again.
STATEMENTS = [
    "ALTER TABLE test_accounts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE test_accounts FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS test_accounts_select ON test_accounts",
    "CREATE POLICY test_accounts_select ON test_accounts FOR SELECT USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT SELECT ON test_accounts TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT SELECT ON test_accounts TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT SELECT ON test_accounts TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT SELECT ON test_accounts TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT SELECT ON test_accounts TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT SELECT ON test_accounts TO rif_probe'; END IF; END $do$",
    """CREATE OR REPLACE FUNCTION reef_test_account_reset() RETURNS timestamptz LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
    me uuid := NULLIF(current_setting('app.person_id', true), '')::uuid;
    stamp timestamptz;
BEGIN
    IF me IS NULL OR NOT EXISTS (SELECT 1 FROM test_accounts WHERE person_id = me) THEN
        RAISE EXCEPTION 'not a test account'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF EXISTS (
        SELECT 1 FROM memberships m JOIN spaces s ON s.id = m.space_id
        WHERE m.person_id = me AND s.owner_person_id <> me
    ) OR EXISTS (
        SELECT 1 FROM memberships o JOIN spaces s ON s.id = o.space_id
        WHERE o.person_id <> me AND (s.owner_person_id = me
            OR s.id IN (SELECT space_id FROM memberships WHERE person_id = me))
    ) THEN
        RAISE EXCEPTION 'shares a space with others'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    DELETE FROM jobs WHERE person_id = me AND status = 'queued';
    DELETE FROM embedding_spend WHERE person_id = me;
    DELETE FROM reading_spend WHERE person_id = me;
    UPDATE test_accounts SET reset_at = now() WHERE person_id = me
    RETURNING reset_at INTO stamp;
    RETURN stamp;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_test_account_reset() OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_test_account_reset() FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON test_accounts TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON spaces TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON embedding_spend TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON reading_spend TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_reset() TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_reset() TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_reset() TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_reset() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_reset() TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_reset() TO rif_probe'; END IF; END $do$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT UPDATE (reset_at) ON test_accounts TO %I', authz); END $authz$",
    """CREATE OR REPLACE FUNCTION reef_test_account_hurry() RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
    me uuid := NULLIF(current_setting('app.person_id', true), '')::uuid;
    moved integer;
BEGIN
    IF me IS NULL OR NOT EXISTS (SELECT 1 FROM test_accounts WHERE person_id = me) THEN
        RETURN 0;
    END IF;
    UPDATE jobs SET run_after = LOCALTIMESTAMP WHERE person_id = me AND status = 'queued';
    GET DIAGNOSTICS moved = ROW_COUNT;
    RETURN moved;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_test_account_hurry() OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_test_account_hurry() FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON test_accounts TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_hurry() TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_hurry() TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_hurry() TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_hurry() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_hurry() TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_test_account_hurry() TO rif_probe'; END IF; END $do$",
]

#: The totals, as they were before but for the line that skips test accounts.
TOTALS = """CREATE OR REPLACE FUNCTION reef_call_log_totals(p_days integer) RETURNS TABLE(method text, outcome text, calls bigint, empty_reasons bigint) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
BEGIN
    IF p_days IS NULL
       OR p_days NOT BETWEEN 7 AND 90 THEN
        RAISE EXCEPTION 'call log totals take 7 to 90 days'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT c.method::text, c.outcome::text, count(*),
           count(*) FILTER (WHERE c.reason = '')
    FROM call_log c
    WHERE c.called_at >= date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => p_days)
      AND c.called_at < date_trunc('day', now() AT TIME ZONE 'UTC')
      AND NOT EXISTS (SELECT 1 FROM test_accounts t WHERE t.person_id = c.person_id)
    GROUP BY c.method, c.outcome
    ORDER BY c.method, c.outcome;
END
$reef$"""


async def forwards() -> MigrationManager:
    """Create ``test_accounts`` with its policy and functions, and the new totals.

    The totals come last: replacing the function keeps its owner and grants,
    and the owner needs the ``SELECT`` on ``test_accounts`` that
    :data:`STATEMENTS` gives it before the totals can read the table.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Create the table, arm it and replace the totals, in one transaction."""
        await run_ddl_atomically([CREATE_TABLE, *STATEMENTS, TOTALS])

    manager.add_raw(run)
    return manager
