"""Rename spaces to coves: tables, columns, policies, helper functions."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef import rls
from reef.db import run_ddl_atomically

ID = "2026-08-20T12:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "spaces become coves, through the schema and its policies"

#: Rename the helper functions **before** anything else moves.
#:
#: Order is the whole trick here. ``ALTER FUNCTION ... RENAME TO`` keeps the
#: OID, and a policy stores the OID rather than the name, so a renamed
#: function carries every policy that calls it along untouched -- the same
#: property the ``rif_`` to ``reef_`` rename leaned on. What that buys is the
#: right to refresh the bodies later with ``CREATE OR REPLACE``: once a
#: function already answers to its new name, replacing it edits that same
#: OID in place. Do it the other way around -- refresh first, under the new
#: name -- and ``CREATE OR REPLACE`` has nothing to replace, so it creates a
#: *second* function while every policy goes on pointing at the first, whose
#: body still names columns that no longer exist.
#:
#: Catalogue-driven rather than a list of signatures, for the reason the
#: previous rename gives: overloads come along for free and a hand-kept list
#: drifts the moment somebody adds a helper. Skips any function whose new
#: name is already taken -- a database where the application applied its DDL
#: first -- and drops nothing, because a stale function a policy still points
#: at is for a human to look at.
RENAME_FUNCTIONS = """
DO $rename$
DECLARE
    target record;
    renamed text;
BEGIN
    FOR target IN
        SELECT function.oid AS oid,
               function.oid::regprocedure AS signature,
               function.proname AS name
        FROM pg_proc AS function
        JOIN pg_namespace AS schema ON schema.oid = function.pronamespace
        WHERE schema.nspname = 'public'
          AND function.proname LIKE 'reef\\_%'
          AND function.proname LIKE '%space%'
    LOOP
        renamed := replace(target.name, 'space', 'cove');
        IF NOT EXISTS (
            SELECT 1
            FROM pg_proc AS existing
            JOIN pg_namespace AS existing_schema
              ON existing_schema.oid = existing.pronamespace
            WHERE existing_schema.nspname = 'public'
              AND existing.proname = renamed
              AND pg_get_function_identity_arguments(existing.oid)
                  = pg_get_function_identity_arguments(target.oid)
        ) THEN
            EXECUTE format(
                'ALTER FUNCTION %s RENAME TO %I', target.signature, renamed
            );
        END IF;
    END LOOP;
END
$rename$
"""

#: Rename the tables, then the columns that point at them.
#:
#: A policy's predicate is stored as a parsed expression tree keyed on
#: attribute numbers, not as the text you typed, so a column rename rewrites
#: every predicate naming that column by itself -- verified on a live server
#: before this was written: ``space_id IN (SELECT ...)`` read
#: ``cove_id IN (SELECT ...)`` afterwards and still filtered. Function bodies
#: are the exception, and the only reason the refresh below exists: those are
#: kept as text and go on naming whatever they named, which is why one of
#: these renames breaks every helper until :func:`_refresh` runs.
#:
#: Guarded on the old name still being there, so a fresh database -- which
#: builds ``coves`` from the start, the migration chain having moved with the
#: models -- runs this as a no-op.
RENAME_TABLES = """
DO $tables$
DECLARE
    moved record;
BEGIN
    FOR moved IN
        SELECT * FROM (VALUES
            ('spaces', 'coves'),
            ('space_appearances', 'cove_appearances')
        ) AS t(old_name, new_name)
    LOOP
        IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public'
                   AND tablename = moved.old_name)
           AND NOT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname = 'public'
                           AND tablename = moved.new_name) THEN
            EXECUTE format('ALTER TABLE %I RENAME TO %I',
                           moved.old_name, moved.new_name);
        END IF;
    END LOOP;
END
$tables$
"""

RENAME_COLUMNS = """
DO $columns$
DECLARE
    moved record;
BEGIN
    FOR moved IN
        SELECT * FROM (VALUES
            ('memberships', 'space_id', 'cove_id'),
            ('pages', 'space_id', 'cove_id'),
            ('attachments', 'space_id', 'cove_id'),
            ('promotions', 'dest_space_id', 'dest_cove_id'),
            ('cove_appearances', 'space_id', 'cove_id')
        ) AS t(table_name, old_name, new_name)
    LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = moved.table_name
              AND column_name = moved.old_name
        ) THEN
            EXECUTE format('ALTER TABLE %I RENAME COLUMN %I TO %I',
                           moved.table_name, moved.old_name, moved.new_name);
        END IF;
    END LOOP;
END
$columns$
"""

#: Rename the policies that carried the old table's name.
#:
#: Cosmetic where the predicates are not: a policy called ``spaces_member_select``
#: on a table called ``coves`` still filters exactly as it did. It is done
#: anyway because :func:`reef.rls.identity_policy_statements` now emits the
#: ``coves_`` spellings, and a database holding both sets would enforce the
#: old ones invisibly alongside the new -- two policies per command, OR'd
#: together, which is how a predicate nobody is reading any more gets to keep
#: granting access.
RENAME_POLICIES = """
DO $policies$
DECLARE
    target record;
    renamed text;
BEGIN
    FOR target IN
        SELECT policy.polname AS name,
               policy.polrelid::regclass AS table_name
        FROM pg_policy AS policy
        WHERE policy.polname LIKE '%space%'
    LOOP
        renamed := replace(target.name, 'space', 'cove');
        IF NOT EXISTS (
            SELECT 1 FROM pg_policy AS existing
            WHERE existing.polrelid = target.table_name::regclass
              AND existing.polname = renamed
        ) THEN
            EXECUTE format('ALTER POLICY %I ON %s RENAME TO %I',
                           target.name, target.table_name, renamed);
        END IF;
    END LOOP;
END
$policies$
"""


def _refresh() -> list[str]:
    """Return the DDL that gives every helper a body naming the new schema.

    Only the groups that create functions and grants -- never the ones that
    create policies. Re-running ``CREATE POLICY`` would fail on policies that
    already exist, and this migration has no business touching them: they
    followed the column rename on their own, and were renamed above.

    :returns: SQL statements to execute in order
    """
    return (
        rls.authz_statements()
        + rls.identity_statements()
        + rls.disclosure_statements()
        + rls.mutation_statements()
        + rls.alias_statements()
        + rls.session_epoch_statements()
    )


async def forwards() -> MigrationManager:
    """Move the schema from ``spaces`` to ``coves`` without a gap in RLS.

    One transaction, because there is a moment in the middle of it where
    every helper function names a column that no longer exists. Committed,
    that state is an outage: a policy calling a broken function raises rather
    than filtering, so reads fail closed for everybody until somebody
    repairs it by hand. Uncommitted, no other session can observe it.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically(
            [
                RENAME_FUNCTIONS,
                RENAME_TABLES,
                RENAME_COLUMNS,
                RENAME_POLICIES,
                *_refresh(),
            ]
        )

    manager.add_raw(run)
    return manager
