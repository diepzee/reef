"""Count calls across people without reading them.

Adds ``reef_call_log_totals(p_days)``: for each method and outcome, how many
calls and how many had no reason, over whole days in UTC, from the start of
the day ``p_days`` days ago to the start of today. It refuses fewer than 7
days or more than the 90 kept. No person, reason, session or time leaves it,
and not how many people either. It is owned by the authz role, so it reads
past row security, and only the app's roles may call it.

The SQL is fixed text, printed from ``reef.rls.call_log_statements()`` on the
day this was written. This module never imports ``reef.rls``: a migration
must run the same SQL on the day it is applied as on the day it was written.
``tests/test_migration_chain.py`` checks that what the chain builds is what
``reef.rls`` declares.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-20T16:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "count calls across people without reading them"

#: Run in order, in one transaction. Each can run again.
STATEMENTS = [
    """CREATE OR REPLACE FUNCTION reef_call_log_totals(p_days integer) RETURNS TABLE(method text, outcome text, calls bigint, empty_reasons bigint) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
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
    WHERE c.called_at >= (date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => p_days)) AT TIME ZONE 'UTC'
      AND c.called_at < date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    GROUP BY c.method, c.outcome
    ORDER BY c.method, c.outcome;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_call_log_totals(p_days integer) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_call_log_totals(p_days integer) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON call_log TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_call_log_totals(p_days integer) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_call_log_totals(p_days integer) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_call_log_totals(p_days integer) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_call_log_totals(p_days integer) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_call_log_totals(p_days integer) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_call_log_totals(p_days integer) TO rif_probe'; END IF; END $do$",
]


async def forwards() -> MigrationManager:
    """Create ``reef_call_log_totals``, owned by the authz role.

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
