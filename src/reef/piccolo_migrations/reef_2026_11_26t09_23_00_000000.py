"""Fix the UTC purge cutoffs and limit call totals to 89 whole days.

Replaces reef_purge_search_log(), reef_purge_call_log() and
reef_call_log_totals(integer) in an existing database using fixed SQL.
Both purges still retain 90 days to the instant; the totals leave the
partially retained oldest day out. CREATE OR REPLACE preserves each
existing owner and every execution grant, including rif_authz and rif_app
on clusters whose roles still use the old names. No rights are granted or
revoked. Tables, stored rows, indexes, policies and all other functions
are left alone. The statements run atomically and are safe to run twice.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-26T09:23:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "use UTC for log purges and stop call totals at 89 days"

#: Fixed SQL; replacing these functions keeps their existing rights and owners.
STATEMENTS = [
    """CREATE OR REPLACE FUNCTION reef_purge_search_log() RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
    removed integer;
BEGIN
    DELETE FROM search_log
    WHERE searched_at < (now() AT TIME ZONE 'UTC') - make_interval(days => 90);
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$reef$""",
    """CREATE OR REPLACE FUNCTION reef_purge_call_log() RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
    removed integer;
BEGIN
    DELETE FROM call_log
    WHERE called_at < (now() AT TIME ZONE 'UTC') - make_interval(days => 90);
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$reef$""",
    """CREATE OR REPLACE FUNCTION reef_call_log_totals(p_days integer) RETURNS TABLE(method text, outcome text, calls bigint, empty_reasons bigint) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
BEGIN
    IF p_days IS NULL
       OR p_days NOT BETWEEN 7 AND 89 THEN
        RAISE EXCEPTION 'call log totals take 7 to 89 days'
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
$reef$""",
]


async def forwards() -> MigrationManager:
    """Replace the three functions without changing their rights.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Replace all three functions in one transaction."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
