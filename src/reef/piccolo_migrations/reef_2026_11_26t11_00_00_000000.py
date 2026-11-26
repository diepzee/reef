"""Give the helper functions a safe search path.

Changes one setting on Reef's own helper functions, the ones that run with
their owner's rights: ``search_path`` becomes ``pg_catalog, public, pg_temp``.
It was ``public, pg_catalog``, which let an object in ``public`` stand in for
a built-in, and which left temporary tables to be searched first.

In an existing database this alters the functions named in :data:`HELPERS`,
whatever their arguments, where they exist. It changes no function's body,
owner or rights, and no table, row, index or policy. Functions that belong
to extensions are not touched. No role is named, so a database whose roles
still have the old names works unchanged. The statement is fixed text, never
imported from ``reef.rls``, runs in one transaction, and is safe to run twice.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-26T11:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "helper functions look in the system catalog first"

#: Every helper that exists when this migration is written. One added later
#: gets the setting from ``reef.rls`` when it is created.
HELPERS = """
        'reef_admit_member',
        'reef_allowlist_person',
        'reef_call_log_totals',
        'reef_claim_job',
        'reef_display_names',
        'reef_invites_minted',
        'reef_member_avatar',
        'reef_member_faces',
        'reef_member_space_ids',
        'reef_oldest_invite',
        'reef_open_door_admit',
        'reef_owns_space',
        'reef_person_alive',
        'reef_person_bind',
        'reef_person_by_email',
        'reef_person_by_subject',
        'reef_person_id_by_email',
        'reef_person_session_epoch',
        'reef_purge_call_log',
        'reef_purge_search_log',
        'reef_remove_member',
        'reef_retire_links',
        'reef_roster',
        'reef_search_log_totals',
        'reef_space_ids',
        'reef_space_owner',
        'reef_test_account_hurry',
        'reef_test_account_reset',
        'reef_transfer_space_ownership'
"""

STATEMENTS = [
    f"""DO $helpers$
DECLARE
    helper regprocedure;
BEGIN
    FOR helper IN
        SELECT p.oid::regprocedure
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.prosecdef
          AND p.proname = ANY (ARRAY[{HELPERS}])
    LOOP
        EXECUTE format(
            'ALTER FUNCTION %s SET search_path = pg_catalog, public, pg_temp',
            helper
        );
    END LOOP;
END
$helpers$"""
]


async def forwards() -> MigrationManager:
    """Set the search path on every helper that exists.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Run the fixed statement atomically."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
