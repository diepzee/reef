"""Count searches and missed searches across people without returning rows.

Reads search_log and engram_access, with the miss rule in searchlog.misses.
Each search counts once, even when several reads were missed. The period
selects searches; a matching read can fall in their following hour today.
Like call totals: 7 to 90 whole UTC days ending at the start of today,
flagged test accounts excluded, authz ownership and app execution only.

The SQL is fixed text printed from reef.rls.search_log_statements when
written. This migration never imports reef.rls or changes existing tables.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-26T09:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "count searches and missed searches across people"

#: Run in order, atomically; safe to run again over existing rows.
STATEMENTS = [
    """CREATE OR REPLACE FUNCTION reef_search_log_totals(p_days integer) RETURNS TABLE(searches bigint, misses bigint) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
BEGIN
    IF p_days IS NULL
       OR p_days NOT BETWEEN 7 AND 90 THEN
        RAISE EXCEPTION 'search log totals take 7 to 90 days'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT count(*), count(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM engram_access a
        WHERE a.person_id = s.person_id AND a.session_id = s.session_id
          AND a.via = 'read' AND a.accessed_at >= s.searched_at
          AND a.accessed_at < s.searched_at + interval '1 hour'
          AND NOT (a.engram_id = ANY (s.engram_ids))
    ))
    FROM search_log s
    WHERE s.searched_at >= date_trunc('day', now() AT TIME ZONE 'UTC') - make_interval(days => p_days)
      AND s.searched_at < date_trunc('day', now() AT TIME ZONE 'UTC')
      AND NOT EXISTS (SELECT 1 FROM test_accounts t WHERE t.person_id = s.person_id);
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_search_log_totals(p_days integer) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_search_log_totals(p_days integer) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON search_log TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON engram_access TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON test_accounts TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_search_log_totals(p_days integer) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_search_log_totals(p_days integer) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_search_log_totals(p_days integer) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_search_log_totals(p_days integer) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_search_log_totals(p_days integer) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_search_log_totals(p_days integer) TO rif_probe'; END IF; END $do$",
]


async def forwards() -> MigrationManager:
    """Create ``reef_search_log_totals``, owned by the authz role.

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
