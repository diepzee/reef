"""Rename the rif_ helper functions to reef_, in place."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-08-19T13:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "rif_* helper functions become reef_*"

#: Rename every ``rif_``-prefixed function this database actually has, by
#: asking the catalogue rather than carrying a list. Overloads are covered
#: for free -- ``reef_allowlist_person`` alone has had three signatures --
#: and a hand-maintained list would drift the moment somebody adds a helper.
#:
#: ``ALTER FUNCTION ... RENAME TO`` keeps the function's OID, and a policy
#: stores the OID rather than the name. So every policy that calls one of
#: these follows the rename by itself: verified against a live server, where
#: a policy reading ``id IN (SELECT probe_ids())`` read
#: ``id IN (SELECT renamed_ids())`` afterwards and kept working. That is what
#: makes this safe to do to the row-level-security boundary -- no policy is
#: dropped, so there is no window where a table is readable without one.
#:
#: A no-op on a database built after this change, which creates the functions
#: under the new names to begin with. Idempotent on re-run.
RENAME = """
DO $rename$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT function.oid AS oid,
               function.oid::regprocedure AS signature,
               function.proname AS name
        FROM pg_proc AS function
        JOIN pg_namespace AS space ON space.oid = function.pronamespace
        WHERE space.nspname = 'public'
          AND function.proname LIKE 'rif\\_%'
    LOOP
        -- Skip when the new name is already taken. That happens on any
        -- database where the application has applied its DDL before this
        -- migration ran: reef.rls creates the helpers under the new names
        -- with CREATE OR REPLACE, so both spellings exist and the rename
        -- would collide. The new one is authoritative; the old one is
        -- vestigial. Nothing is dropped here -- a stale function still
        -- referenced by a policy is a problem for a human to look at, not
        -- something a migration should silently remove.
        IF NOT EXISTS (
            SELECT 1
            FROM pg_proc AS existing
            JOIN pg_namespace AS existing_space
              ON existing_space.oid = existing.pronamespace
            WHERE existing_space.nspname = 'public'
              AND existing.proname = 'reef_' || substring(target.name FROM 5)
              AND pg_get_function_identity_arguments(existing.oid)
                  = pg_get_function_identity_arguments(target.oid)
        ) THEN
            EXECUTE format(
                'ALTER FUNCTION %s RENAME TO %I',
                target.signature,
                'reef_' || substring(target.name FROM 5)
            );
        END IF;
    END LOOP;
END
$rename$
"""


async def forwards() -> MigrationManager:
    """Rename the helper functions without touching a single policy.

    The functions are the row-level-security boundary: policies call them to
    decide which rows a person may see. Dropping and recreating them would
    mean a moment with no policy predicate, which is exactly the failure this
    schema exists to prevent. Renaming in place avoids that entirely.

    Roles (``rif``, ``reef_authz``, ``rif_probe``) and the migration app name
    are deliberately untouched here. Those are separate changes with separate
    rollbacks.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically([RENAME])

    manager.add_raw(run)
    return manager
