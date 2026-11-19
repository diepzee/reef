"""Rename cove to space and page to engram, through the schema and its policies."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef import rls
from reef.db import run_ddl_atomically

ID = "2026-11-20T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "coves become spaces and pages become engrams, through the schema"

# Every statement from here to _refresh() was printed by scripts/rename_sql.py,
# run against two databases: one built by this migration chain from nothing,
# and one built the way production was -- the chain as it stood before the
# August rename to cove, then the rest. The two differ only in the names of
# indexes and constraints, which is why those statements are guarded and the
# rest are not.
#
# Production, measured on 19 November 2026, has 6 tables, 14 columns,
# 5 helper functions, 17 policies and 10 indexes whose names say cove or page.
# The production-like build has exactly those numbers.

#: The six tables. Grants, row security and the policies on a table follow
#: it, because Postgres keys them on the table's OID rather than its name.
RENAME_TABLES = """
ALTER TABLE cove_appearances RENAME TO space_appearances;
ALTER TABLE coves RENAME TO spaces;
ALTER TABLE page_access RENAME TO engram_access;
ALTER TABLE page_embeddings RENAME TO engram_embeddings;
ALTER TABLE page_heat RENAME TO engram_heat;
ALTER TABLE pages RENAME TO engrams;
"""

#: The fourteen columns. A policy's predicate is a parsed expression keyed on
#: column numbers, so it follows a renamed column by itself. Function bodies
#: are kept as text and do not: :func:`_refresh` rewrites them.
RENAME_COLUMNS = """
ALTER TABLE attachments RENAME COLUMN cove_id TO space_id;
ALTER TABLE attachments RENAME COLUMN page_id TO engram_id;
ALTER TABLE space_appearances RENAME COLUMN cove_id TO space_id;
ALTER TABLE links RENAME COLUMN from_page_id TO from_engram_id;
ALTER TABLE links RENAME COLUMN to_page_id TO to_engram_id;
ALTER TABLE memberships RENAME COLUMN cove_id TO space_id;
ALTER TABLE engram_access RENAME COLUMN page_id TO engram_id;
ALTER TABLE engram_embeddings RENAME COLUMN page_id TO engram_id;
ALTER TABLE engram_heat RENAME COLUMN page_id TO engram_id;
ALTER TABLE engrams RENAME COLUMN cove_id TO space_id;
ALTER TABLE promotions RENAME COLUMN dest_cove_id TO dest_space_id;
ALTER TABLE promotions RENAME COLUMN source_page_id TO source_engram_id;
ALTER TABLE revisions RENAME COLUMN page_id TO engram_id;
ALTER TABLE search_log RENAME COLUMN page_ids TO engram_ids;
"""

#: The five helper functions named after a cove. Renaming keeps each one's
#: OID, owner and grants, so the policies that call them stay valid until
#: they are replaced below, and nothing is left behind under the old name.
RENAME_FUNCTIONS = """
ALTER FUNCTION reef_cove_ids() RENAME TO reef_space_ids;
ALTER FUNCTION reef_cove_owner(uuid) RENAME TO reef_space_owner;
ALTER FUNCTION reef_member_cove_ids() RENAME TO reef_member_space_ids;
ALTER FUNCTION reef_owns_cove(uuid) RENAME TO reef_owns_space;
ALTER FUNCTION reef_transfer_cove_ownership(uuid, uuid) RENAME TO reef_transfer_space_ownership;
"""

#: Indexes, including those behind a primary key or unique constraint, which
#: take the constraint's name along. Guarded, because the names depend on
#: when the table was made: production's say ``pages_space_id`` where a
#: database built from scratch says ``pages_cove_id``. Both end up as
#: ``engrams_space_id``, the name the suite's schema gives it.
RENAME_INDEXES = """
ALTER INDEX IF EXISTS attachments_cove_id RENAME TO attachments_space_id;
ALTER INDEX IF EXISTS cove_appearances_person_cove RENAME TO space_appearances_person_space;
ALTER INDEX IF EXISTS cove_appearances_pkey RENAME TO space_appearances_pkey;
ALTER INDEX IF EXISTS coves_pkey RENAME TO spaces_pkey;
ALTER INDEX IF EXISTS memberships_person_cove RENAME TO memberships_person_space;
ALTER INDEX IF EXISTS page_access_pkey RENAME TO engram_access_pkey;
ALTER INDEX IF EXISTS page_access_recent RENAME TO engram_access_recent;
ALTER INDEX IF EXISTS page_embeddings_piece RENAME TO engram_embeddings_piece;
ALTER INDEX IF EXISTS page_embeddings_pkey RENAME TO engram_embeddings_pkey;
ALTER INDEX IF EXISTS page_heat_person_page RENAME TO engram_heat_person_engram;
ALTER INDEX IF EXISTS page_heat_pkey RENAME TO engram_heat_pkey;
ALTER INDEX IF EXISTS pages_cove_id RENAME TO engrams_space_id;
ALTER INDEX IF EXISTS pages_cove_path RENAME TO engrams_space_path;
ALTER INDEX IF EXISTS pages_pkey RENAME TO engrams_pkey;
ALTER INDEX IF EXISTS pages_space_id RENAME TO engrams_space_id;
ALTER INDEX IF EXISTS pages_space_path RENAME TO engrams_space_path;
ALTER INDEX IF EXISTS revisions_page_id RENAME TO revisions_engram_id;
"""

#: Foreign key, check and not-null constraints, guarded for the same reason.
#: A name missing here is only a name: it constrains exactly what it did.
RENAME_CONSTRAINTS = """
DO $constraints$
DECLARE
    moved record;
BEGIN
    FOR moved IN
        SELECT * FROM (VALUES
            ('attachments', 'attachments_cove_id_fkey', 'attachments_space_id_fkey'),
            ('attachments', 'attachments_page_id_fkey', 'attachments_engram_id_fkey'),
            ('space_appearances', 'cove_appearances_cove_id_fkey', 'space_appearances_space_id_fkey'),
            ('space_appearances', 'cove_appearances_cove_id_not_null', 'space_appearances_space_id_not_null'),
            ('space_appearances', 'cove_appearances_id_not_null', 'space_appearances_id_not_null'),
            ('space_appearances', 'cove_appearances_person_id_fkey', 'space_appearances_person_id_fkey'),
            ('space_appearances', 'cove_appearances_person_id_not_null', 'space_appearances_person_id_not_null'),
            ('spaces', 'coves_id_not_null', 'spaces_id_not_null'),
            ('spaces', 'coves_kind_not_null', 'spaces_kind_not_null'),
            ('spaces', 'coves_owner_person_id_fkey', 'spaces_owner_person_id_fkey'),
            ('spaces', 'coves_owner_person_id_not_null', 'spaces_owner_person_id_not_null'),
            ('spaces', 'coves_slug_not_null', 'spaces_slug_not_null'),
            ('spaces', 'coves_version_not_null', 'spaces_version_not_null'),
            ('links', 'links_from_page_id_fkey', 'links_from_engram_id_fkey'),
            ('links', 'links_from_page_id_not_null', 'links_from_engram_id_not_null'),
            ('links', 'links_to_page_id_fkey', 'links_to_engram_id_fkey'),
            ('links', 'links_to_page_id_not_null', 'links_to_engram_id_not_null'),
            ('memberships', 'memberships_cove_id_fkey', 'memberships_space_id_fkey'),
            ('engram_access', 'page_access_accessed_at_not_null', 'engram_access_accessed_at_not_null'),
            ('engram_access', 'page_access_id_not_null', 'engram_access_id_not_null'),
            ('engram_access', 'page_access_page_id_fkey', 'engram_access_engram_id_fkey'),
            ('engram_access', 'page_access_page_id_not_null', 'engram_access_engram_id_not_null'),
            ('engram_access', 'page_access_person_id_fkey', 'engram_access_person_id_fkey'),
            ('engram_access', 'page_access_person_id_not_null', 'engram_access_person_id_not_null'),
            ('engram_access', 'page_access_via_known', 'engram_access_via_known'),
            ('engram_access', 'page_access_via_not_null', 'engram_access_via_not_null'),
            ('engram_access', 'page_access_weight_not_null', 'engram_access_weight_not_null'),
            ('engram_access', 'page_access_weight_sane', 'engram_access_weight_sane'),
            ('engram_embeddings', 'page_embeddings_content_hash_not_null', 'engram_embeddings_content_hash_not_null'),
            ('engram_embeddings', 'page_embeddings_created_at_not_null', 'engram_embeddings_created_at_not_null'),
            ('engram_embeddings', 'page_embeddings_dims', 'engram_embeddings_dims'),
            ('engram_embeddings', 'page_embeddings_dims_not_null', 'engram_embeddings_dims_not_null'),
            ('engram_embeddings', 'page_embeddings_embedding_not_null', 'engram_embeddings_embedding_not_null'),
            ('engram_embeddings', 'page_embeddings_id_not_null', 'engram_embeddings_id_not_null'),
            ('engram_embeddings', 'page_embeddings_model_id_not_null', 'engram_embeddings_model_id_not_null'),
            ('engram_embeddings', 'page_embeddings_page_id_fkey', 'engram_embeddings_engram_id_fkey'),
            ('engram_embeddings', 'page_embeddings_page_id_not_null', 'engram_embeddings_engram_id_not_null'),
            ('engram_heat', 'page_heat_accesses_not_null', 'engram_heat_accesses_not_null'),
            ('engram_heat', 'page_heat_activation_not_null', 'engram_heat_activation_not_null'),
            ('engram_heat', 'page_heat_id_not_null', 'engram_heat_id_not_null'),
            ('engram_heat', 'page_heat_last_access_not_null', 'engram_heat_last_access_not_null'),
            ('engram_heat', 'page_heat_page_id_fkey', 'engram_heat_engram_id_fkey'),
            ('engram_heat', 'page_heat_page_id_not_null', 'engram_heat_engram_id_not_null'),
            ('engram_heat', 'page_heat_person_id_fkey', 'engram_heat_person_id_fkey'),
            ('engram_heat', 'page_heat_person_id_not_null', 'engram_heat_person_id_not_null'),
            ('engram_heat', 'page_heat_updated_at_not_null', 'engram_heat_updated_at_not_null'),
            ('engrams', 'pages_body_not_null', 'engrams_body_not_null'),
            ('engrams', 'pages_cove_id_fkey', 'engrams_space_id_fkey'),
            ('engrams', 'pages_created_at_not_null', 'engrams_created_at_not_null'),
            ('engrams', 'pages_id_not_null', 'engrams_id_not_null'),
            ('engrams', 'pages_path_not_null', 'engrams_path_not_null'),
            ('engrams', 'pages_space_id_fkey', 'engrams_space_id_fkey'),
            ('engrams', 'pages_tags_not_null', 'engrams_tags_not_null'),
            ('engrams', 'pages_title_not_null', 'engrams_title_not_null'),
            ('engrams', 'pages_updated_at_not_null', 'engrams_updated_at_not_null'),
            ('engrams', 'pages_version_not_null', 'engrams_version_not_null'),
            ('promotions', 'promotions_dest_cove_id_fkey', 'promotions_dest_space_id_fkey'),
            ('promotions', 'promotions_dest_cove_id_not_null', 'promotions_dest_space_id_not_null'),
            ('promotions', 'promotions_source_page_id_fkey', 'promotions_source_engram_id_fkey'),
            ('revisions', 'revisions_page_id_fkey', 'revisions_engram_id_fkey'),
            ('search_log', 'search_log_page_ids_not_null', 'search_log_engram_ids_not_null')
        ) AS t(table_name, old_name, new_name)
    LOOP
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = format('public.%I', moved.table_name)::regclass
              AND conname = moved.old_name
        ) THEN
            EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I',
                           moved.table_name, moved.old_name, moved.new_name);
        END IF;
    END LOOP;
END
$constraints$;
"""

#: Every policy, so :func:`_refresh` can create each one again from
#: :mod:`reef.rls`. Renaming them would not be enough. A policy that calls a
#: helper in a subquery stores the helper's name as a column label, and that
#: label keeps saying ``reef_cove_ids`` after the rename. It is cosmetic, but
#: it makes production's policies read differently from the ones the suite
#: tests, which ``tests/test_migration_chain.py`` rightly refuses.
DROP_POLICIES = """
DROP POLICY IF EXISTS attachments_delete ON attachments;
DROP POLICY IF EXISTS attachments_insert ON attachments;
DROP POLICY IF EXISTS attachments_select ON attachments;
DROP POLICY IF EXISTS attachments_update ON attachments;
DROP POLICY IF EXISTS cove_appearances_self ON space_appearances;
DROP POLICY IF EXISTS coves_member_select ON spaces;
DROP POLICY IF EXISTS coves_member_update ON spaces;
DROP POLICY IF EXISTS coves_owner_delete ON spaces;
DROP POLICY IF EXISTS coves_owner_insert ON spaces;
DROP POLICY IF EXISTS coves_owner_select ON spaces;
DROP POLICY IF EXISTS embedding_spend_owner ON embedding_spend;
DROP POLICY IF EXISTS jobs_owner ON jobs;
DROP POLICY IF EXISTS links_insert ON links;
DROP POLICY IF EXISTS links_select ON links;
DROP POLICY IF EXISTS memberships_covis_select ON memberships;
DROP POLICY IF EXISTS memberships_insert ON memberships;
DROP POLICY IF EXISTS memberships_self_delete ON memberships;
DROP POLICY IF EXISTS memberships_self_select ON memberships;
DROP POLICY IF EXISTS memberships_self_update ON memberships;
DROP POLICY IF EXISTS page_access_delete ON engram_access;
DROP POLICY IF EXISTS page_access_insert ON engram_access;
DROP POLICY IF EXISTS page_access_select ON engram_access;
DROP POLICY IF EXISTS page_embeddings_delete ON engram_embeddings;
DROP POLICY IF EXISTS page_embeddings_insert ON engram_embeddings;
DROP POLICY IF EXISTS page_embeddings_select ON engram_embeddings;
DROP POLICY IF EXISTS page_heat_owner ON engram_heat;
DROP POLICY IF EXISTS pages_delete ON engrams;
DROP POLICY IF EXISTS pages_insert ON engrams;
DROP POLICY IF EXISTS pages_select ON engrams;
DROP POLICY IF EXISTS pages_update ON engrams;
DROP POLICY IF EXISTS persons_invite_insert ON persons;
DROP POLICY IF EXISTS persons_self_delete ON persons;
DROP POLICY IF EXISTS persons_self_select ON persons;
DROP POLICY IF EXISTS persons_self_update ON persons;
DROP POLICY IF EXISTS promotions_owner ON promotions;
DROP POLICY IF EXISTS revisions_delete ON revisions;
DROP POLICY IF EXISTS revisions_insert ON revisions;
DROP POLICY IF EXISTS revisions_select ON revisions;
DROP POLICY IF EXISTS revisions_update ON revisions;
DROP POLICY IF EXISTS search_log_delete ON search_log;
DROP POLICY IF EXISTS search_log_insert ON search_log;
DROP POLICY IF EXISTS search_log_select ON search_log;
"""

#: Helpers whose parameters are named ``p_cove`` or ``p_page``.
#: ``CREATE OR REPLACE FUNCTION`` cannot rename a parameter, so these are
#: dropped and created again by :func:`_refresh`. No policy calls them, and
#: by now no policy exists anyway.
#:
#: ``reef_member_avatar`` and ``reef_member_faces`` are on the list although
#: production may not have them: in the production-like build, the August
#: rename dropped them as stale and never created them again. The refresh
#: creates them either way.
DROP_RENAMED_PARAMETERS = """
DROP FUNCTION IF EXISTS reef_admit_member(uuid, uuid, text, text);
DROP FUNCTION IF EXISTS reef_space_owner(uuid);
DROP FUNCTION IF EXISTS reef_member_avatar(uuid, uuid);
DROP FUNCTION IF EXISTS reef_member_faces(uuid);
DROP FUNCTION IF EXISTS reef_remove_member(uuid, uuid);
DROP FUNCTION IF EXISTS reef_retire_links(uuid);
DROP FUNCTION IF EXISTS reef_roster(uuid);
DROP FUNCTION IF EXISTS reef_transfer_space_ownership(uuid, uuid);
"""


def _refresh() -> list[str]:
    """Return today's security DDL, which names spaces and engrams.

    The same groups ``tests/conftest.py`` applies, in the same order, apart
    from :func:`reef.rls.constraint_statements` and
    :func:`reef.search.search_statements`: their objects already exist,
    renamed above, and adding them again would fail. Every function is
    created or replaced, every policy created, every grant given again.

    :returns: SQL statements to execute in order
    """
    return (
        rls.enable_statements()
        + rls.appearance_statements()
        + rls.job_statements()
        + rls.link_statements()
        + rls.usage_statements()
        + rls.search_log_statements()
        + rls.embedding_statements()
        + rls.session_epoch_statements()
        + rls.alias_statements()
        + rls.open_door_statements()
        + rls.avatar_statements()
        + rls.person_column_grant_statements()
    )


async def forwards() -> MigrationManager:
    """Rename the schema and apply today's policies, in one transaction.

    Between dropping the policies and creating them again, the content
    tables have row security forced and no policy at all, which denies every
    row to everyone. Inside one transaction nobody can see that moment: other
    sessions wait on the table locks and then see the finished schema. If any
    statement fails, all of it rolls back and the old schema stays as it was.

    The version still serving when this commits names tables that no longer
    exist, so its requests fail -- with an error, never with somebody else's
    rows -- until the new version takes over. The container runs this just
    before it starts the new version, which keeps that to the switch-over.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically(
            [
                RENAME_TABLES,
                RENAME_COLUMNS,
                RENAME_FUNCTIONS,
                RENAME_INDEXES,
                RENAME_CONSTRAINTS,
                DROP_POLICIES,
                DROP_RENAMED_PARAMETERS,
                *_refresh(),
            ]
        )

    manager.add_raw(run)
    return manager
