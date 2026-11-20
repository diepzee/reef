"""Rename cove to space and page to engram, through the schema and its policies."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-20T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "coves become spaces and pages become engrams, through the schema"

# Every statement from here to REFRESH was printed by scripts/rename_sql.py,
# run against two databases: one built by this migration chain from nothing,
# and one built the way production was -- the chain as it stood before the
# August rename to cove, then the rest. The two differ only in the names of
# indexes and constraints, which is why those statements are guarded and the
# rest are not.
#
# Production, measured on 19 November 2026, has 6 tables, 14 columns,
# 5 helper functions, 17 policies and 10 indexes whose names say cove or page.
# The production-like build has exactly those numbers.
#
# scripts/rehearse_rename.py builds both databases again, checks that
# rename_sql.py still prints these statements, runs this migration on seeded
# rows and checks the result. Run it before deploying.

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
#: are kept as text and do not: :data:`REFRESH` rewrites them.
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

#: Every policy, so :data:`REFRESH` can create each one again as
#: :mod:`reef.rls` wrote it on the day. Renaming them would not be enough. A policy that calls a
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
#: dropped and created again by :data:`REFRESH`. No policy calls them, and
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


#: The security DDL from :mod:`reef.rls` as it stood on 20 November 2026,
#: printed once from the groups ``tests/conftest.py`` applies, in its order,
#: apart from ``constraint_statements`` and ``search_statements``: their
#: objects already exist, renamed above, and adding them again would fail.
#: Every helper is created or replaced, every policy created and every grant
#: given again, so each helper's body names the new tables.
#:
#: Fixed text, never an import. A migration that reads ``reef.rls`` at run
#: time applies whatever the module says on the day a database is built, so
#: a later edit to a predicate would reach every fresh build through here
#: and never production, while ``tests/test_migration_chain.py`` reported
#: the two as the same. A change to ``reef.rls`` needs a migration of its own.
REFRESH = [
    "CREATE OR REPLACE FUNCTION reef_space_ids() RETURNS SETOF uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT space_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_space_ids() OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_space_ids() FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_ids() TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_ids() TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_ids() TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_ids() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_ids() TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_ids() TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_member_space_ids() RETURNS SETOF uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT space_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_member_space_ids() OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_member_space_ids() FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_space_ids() TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_space_ids() TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_space_ids() TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_space_ids() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_space_ids() TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_space_ids() TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_person_by_subject(p_subject text) RETURNS TABLE(person_id uuid, person_email text, person_display_name text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.id, p.email, p.display_name FROM persons p WHERE p.subject = p_subject$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_person_by_subject(p_subject text) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_person_by_subject(p_subject text) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_subject(p_subject text) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_subject(p_subject text) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_subject(p_subject text) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_subject(p_subject text) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_subject(p_subject text) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_subject(p_subject text) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_person_by_email(p_email text) RETURNS TABLE(person_id uuid, person_email text, person_display_name text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.id, p.email, p.display_name FROM persons p WHERE p.email = lower(p_email)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_person_by_email(p_email text) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_person_by_email(p_email text) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_email(p_email text) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_email(p_email text) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_email(p_email text) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_email(p_email text) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_email(p_email text) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_by_email(p_email text) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_person_bind(p_email text, p_subject text) RETURNS TABLE(person_id uuid, person_email text, person_display_name text) LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$UPDATE persons SET subject = p_subject WHERE email = lower(p_email) AND subject IS NULL RETURNING id, email, display_name$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_person_bind(p_email text, p_subject text) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_person_bind(p_email text, p_subject text) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_bind(p_email text, p_subject text) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_bind(p_email text, p_subject text) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_bind(p_email text, p_subject text) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_bind(p_email text, p_subject text) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_bind(p_email text, p_subject text) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_bind(p_email text, p_subject text) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_person_alive(p_id uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT EXISTS (SELECT 1 FROM persons WHERE id = p_id)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_person_alive(p_id uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_person_alive(p_id uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_alive(p_id uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_alive(p_id uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_alive(p_id uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_alive(p_id uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_alive(p_id uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_alive(p_id uuid) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_roster(p_space uuid) RETURNS TABLE(person_id uuid, member_name text, member_email text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.id, p.display_name, CASE WHEN EXISTS (SELECT 1 FROM spaces s WHERE s.id = p_space AND s.owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) THEN p.email ELSE '' END FROM persons p WHERE p.id IN (SELECT person_id FROM memberships WHERE space_id = p_space) AND p_space IN (SELECT space_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) ORDER BY p.display_name$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_roster(p_space uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_roster(p_space uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON spaces TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_roster(p_space uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_roster(p_space uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_roster(p_space uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_roster(p_space uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_roster(p_space uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_roster(p_space uuid) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_space_owner(p_space uuid) RETURNS TABLE(owner_name text, owner_email text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.display_name, p.email FROM persons p JOIN spaces s ON s.owner_person_id = p.id WHERE s.id = p_space AND p_space IN (SELECT space_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_space_owner(p_space uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_space_owner(p_space uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON spaces TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_owner(p_space uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_owner(p_space uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_owner(p_space uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_owner(p_space uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_owner(p_space uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_space_owner(p_space uuid) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_display_names(p_ids uuid[]) RETURNS TABLE(person_id uuid, display_name text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.id, p.display_name FROM persons p WHERE p.id = ANY(p_ids)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_display_names(p_ids uuid[]) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_display_names(p_ids uuid[]) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_display_names(p_ids uuid[]) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_display_names(p_ids uuid[]) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_display_names(p_ids uuid[]) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_display_names(p_ids uuid[]) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_display_names(p_ids uuid[]) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_display_names(p_ids uuid[]) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_person_id_by_email(p_email text) RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT id FROM persons WHERE email = lower(p_email)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_person_id_by_email(p_email text) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_person_id_by_email(p_email text) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_id_by_email(p_email text) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_id_by_email(p_email text) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_id_by_email(p_email text) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_id_by_email(p_email text) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_id_by_email(p_email text) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_id_by_email(p_email text) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_invites_minted(p_window_days int) RETURNS bigint LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT count(*) FROM persons WHERE invited_by_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND created_at >= LOCALTIMESTAMP - make_interval(days => p_window_days)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_invites_minted(p_window_days int) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_invites_minted(p_window_days int) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_invites_minted(p_window_days int) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_invites_minted(p_window_days int) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_invites_minted(p_window_days int) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_invites_minted(p_window_days int) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_invites_minted(p_window_days int) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_invites_minted(p_window_days int) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_oldest_invite(p_window_days int) RETURNS timestamp LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT min(created_at) FROM persons WHERE invited_by_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND created_at >= LOCALTIMESTAMP - make_interval(days => p_window_days)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_oldest_invite(p_window_days int) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_oldest_invite(p_window_days int) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_oldest_invite(p_window_days int) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_oldest_invite(p_window_days int) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_oldest_invite(p_window_days int) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_oldest_invite(p_window_days int) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_oldest_invite(p_window_days int) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_oldest_invite(p_window_days int) TO rif_probe'; END IF; END $do$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', authz); END $authz$",
    "DROP FUNCTION IF EXISTS reef_admit_member(uuid, uuid, text)",
    "CREATE OR REPLACE FUNCTION reef_owns_space(p_space uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT EXISTS (SELECT 1 FROM spaces WHERE id = p_space AND owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_owns_space(p_space uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_owns_space(p_space uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON spaces TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_owns_space(p_space uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_owns_space(p_space uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_owns_space(p_space uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_owns_space(p_space uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_owns_space(p_space uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_owns_space(p_space uuid) TO rif_probe'; END IF; END $do$",
    """CREATE OR REPLACE FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) RETURNS uuid LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
  new_id uuid;
  spent int;
BEGIN
  -- Unarmed means nobody is accountable for the invitation, and the row
  -- would land with a NULL inviter -- indistinguishable from the founding
  -- person, and outside the budget. Refuse rather than create it.
  IF NULLIF(current_setting('app.person_id', true), '')::uuid IS NULL THEN
    RETURN NULL;
  END IF;

  -- The budget is enforced here, not by the caller. Checking it in Python
  -- and inserting afterwards is a check-then-act: two invitations racing on
  -- the last slot both see one free and both land. Locking the inviter's
  -- own row serialises those attempts, and the count and the insert then
  -- happen with nobody able to interleave.
  PERFORM 1 FROM persons WHERE id = NULLIF(current_setting('app.person_id', true), '')::uuid FOR UPDATE;

  -- The clock is the database's. An earlier draft took it from the caller
  -- so it would agree with Python's, which made an arbitrary timestamp part
  -- of the caller's authority: backdate a row and it never counts again.
  SELECT count(*) INTO spent FROM persons
   WHERE invited_by_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid
     AND created_at >= LOCALTIMESTAMP - make_interval(days => p_window_days);
  IF spent >= p_budget THEN
    RETURN NULL;
  END IF;

  INSERT INTO persons (id, email, display_name, invited_by_person_id, created_at)
  VALUES (gen_random_uuid(), lower(p_email), p_display_name, NULLIF(current_setting('app.person_id', true), '')::uuid,
          LOCALTIMESTAMP)
  RETURNING id INTO new_id;
  RETURN new_id;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_allowlist_person(p_email text, p_display_name text, p_window_days int, p_budget int) TO rif_probe'; END IF; END $do$",
    """CREATE OR REPLACE FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) RETURNS record LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
  affected int;
BEGIN
  removed := false;
  person_erased := false;
  -- Only the space's owner, and never on themselves: an owner removing
  -- themselves would leave the space with no accountable person.
  IF NOT EXISTS (SELECT 1 FROM spaces WHERE id = p_space
                 AND owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) THEN
    RETURN;
  END IF;
  IF p_person = NULLIF(current_setting('app.person_id', true), '')::uuid THEN
    RETURN;
  END IF;
  DELETE FROM memberships WHERE space_id = p_space AND person_id = p_person;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected = 0 THEN
    RETURN;
  END IF;
  removed := true;
  -- An invitation that was never taken up: no provider subject bound, and
  -- now no membership anywhere. Erasing it is the typo-repair path.
  IF EXISTS (SELECT 1 FROM persons WHERE id = p_person AND subject IS NULL)
     AND NOT EXISTS (SELECT 1 FROM memberships WHERE person_id = p_person) THEN
    DELETE FROM persons WHERE id = p_person;
    person_erased := true;
  END IF;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON spaces TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) TO rif_probe'; END IF; END $do$",
    """CREATE OR REPLACE FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM spaces WHERE id = p_space
                 AND owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) THEN
    RETURN false;
  END IF;
  -- The successor must already belong to the space. Without this an owner
  -- could hand a space to any uuid that satisfies the foreign key, including
  -- somebody who has never seen it.
  IF NOT EXISTS (SELECT 1 FROM memberships WHERE space_id = p_space
                 AND person_id = p_successor) THEN
    RETURN false;
  END IF;
  UPDATE memberships SET role = 'member'
   WHERE space_id = p_space AND person_id = p_successor AND role <> 'member';
  UPDATE spaces SET owner_person_id = p_successor WHERE id = p_space;
  RETURN true;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON spaces TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_transfer_space_ownership(p_space uuid, p_successor uuid) TO rif_probe'; END IF; END $do$",
    "ALTER TABLE engrams ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE engrams FORCE ROW LEVEL SECURITY",
    "CREATE POLICY engrams_select ON engrams FOR SELECT USING (space_id IN (SELECT reef_space_ids()))",
    "CREATE POLICY engrams_insert ON engrams FOR INSERT WITH CHECK (space_id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY engrams_update ON engrams FOR UPDATE USING (space_id IN (SELECT reef_member_space_ids())) WITH CHECK (space_id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY engrams_delete ON engrams FOR DELETE USING (space_id IN (SELECT reef_member_space_ids()))",
    "ALTER TABLE attachments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE attachments FORCE ROW LEVEL SECURITY",
    "CREATE POLICY attachments_select ON attachments FOR SELECT USING (space_id IN (SELECT reef_space_ids()))",
    "CREATE POLICY attachments_insert ON attachments FOR INSERT WITH CHECK (space_id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY attachments_update ON attachments FOR UPDATE USING (space_id IN (SELECT reef_member_space_ids())) WITH CHECK (space_id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY attachments_delete ON attachments FOR DELETE USING (space_id IN (SELECT reef_member_space_ids()))",
    "ALTER TABLE revisions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE revisions FORCE ROW LEVEL SECURITY",
    "CREATE POLICY revisions_select ON revisions FOR SELECT USING (engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_space_ids())))",
    "CREATE POLICY revisions_insert ON revisions FOR INSERT WITH CHECK (engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE POLICY revisions_update ON revisions FOR UPDATE USING (engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids()))) WITH CHECK (engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE POLICY revisions_delete ON revisions FOR DELETE USING (engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids())))",
    "ALTER TABLE promotions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE promotions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS promotions_owner ON promotions",
    "CREATE POLICY promotions_owner ON promotions USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "ALTER TABLE persons ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE persons FORCE ROW LEVEL SECURITY",
    "ALTER TABLE spaces ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE spaces FORCE ROW LEVEL SECURITY",
    "ALTER TABLE memberships ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE memberships FORCE ROW LEVEL SECURITY",
    "CREATE POLICY persons_self_select ON persons FOR SELECT USING (id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY persons_self_update ON persons FOR UPDATE USING (id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY persons_self_delete ON persons FOR DELETE USING (id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY persons_invite_insert ON persons FOR INSERT WITH CHECK (invited_by_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY spaces_member_select ON spaces FOR SELECT USING (id IN (SELECT reef_space_ids()))",
    "CREATE POLICY spaces_owner_select ON spaces FOR SELECT USING (owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY spaces_owner_insert ON spaces FOR INSERT WITH CHECK (owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY spaces_member_update ON spaces FOR UPDATE USING (id IN (SELECT reef_member_space_ids())) WITH CHECK (id IN (SELECT reef_member_space_ids()))",
    "CREATE POLICY spaces_owner_delete ON spaces FOR DELETE USING (owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY memberships_self_select ON memberships FOR SELECT USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY memberships_covis_select ON memberships FOR SELECT USING (space_id IN (SELECT reef_space_ids()))",
    "CREATE POLICY memberships_insert ON memberships FOR INSERT WITH CHECK (reef_owns_space(space_id))",
    "CREATE POLICY memberships_self_delete ON memberships FOR DELETE USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY memberships_self_update ON memberships FOR UPDATE USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'REVOKE UPDATE ON spaces FROM reef_app'; EXECUTE 'GRANT UPDATE (version) ON spaces TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'REVOKE UPDATE ON spaces FROM reef'; EXECUTE 'GRANT UPDATE (version) ON spaces TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'REVOKE UPDATE ON spaces FROM reef_probe'; EXECUTE 'GRANT UPDATE (version) ON spaces TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'REVOKE UPDATE ON spaces FROM rif_app'; EXECUTE 'GRANT UPDATE (version) ON spaces TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'REVOKE UPDATE ON spaces FROM rif'; EXECUTE 'GRANT UPDATE (version) ON spaces TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'REVOKE UPDATE ON spaces FROM rif_probe'; EXECUTE 'GRANT UPDATE (version) ON spaces TO rif_probe'; END IF; END $do$",
    "ALTER TABLE space_appearances ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE space_appearances FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS space_appearances_self ON space_appearances",
    "CREATE POLICY space_appearances_self ON space_appearances USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_known",
    "ALTER TABLE jobs ADD CONSTRAINT jobs_status_known CHECK (status IN ('queued', 'running', 'done', 'failed'))",
    "CREATE INDEX IF NOT EXISTS jobs_claimable ON jobs (run_after, created_at) WHERE status IN ('queued', 'running')",
    "CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_queued_per_person_kind ON jobs (person_id, kind) WHERE status = 'queued' AND cardinality(subject_ids) = 0",
    "ALTER TABLE jobs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE jobs FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS jobs_owner ON jobs",
    "CREATE POLICY jobs_owner ON jobs USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    """CREATE OR REPLACE FUNCTION reef_claim_job(worker text) RETURNS TABLE(job_id uuid, job_kind text, job_person_id uuid, job_subject_ids uuid[], job_attempts integer) LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
BEGIN
    -- A job whose worker died on its last allowed attempt would otherwise
    -- stay 'running' forever: nothing may claim it again, and the worker
    -- that would have recorded the failure is gone.
    UPDATE jobs SET status = 'failed', error = 'LeaseExpired',
        locked_by = NULL, locked_at = NULL, finished_at = LOCALTIMESTAMP
    WHERE id IN (
        SELECT d.id FROM jobs d
        WHERE d.status = 'running'
          AND d.attempts >= 5
          AND d.locked_at < LOCALTIMESTAMP - interval '300 seconds'
        FOR UPDATE SKIP LOCKED
    );
    RETURN QUERY
    UPDATE jobs j SET status = 'running', attempts = j.attempts + 1,
        locked_by = left(worker, 255), locked_at = LOCALTIMESTAMP
    WHERE j.id = (
        SELECT c.id FROM jobs c
        WHERE (c.status = 'queued' AND c.run_after <= LOCALTIMESTAMP)
           OR (c.status = 'running' AND c.locked_at
               < LOCALTIMESTAMP - interval '300 seconds')
        ORDER BY c.run_after, c.created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING j.id, j.kind::text, j.person_id, j.subject_ids, j.attempts;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_claim_job(worker text) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_claim_job(worker text) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_claim_job(worker text) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_claim_job(worker text) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_claim_job(worker text) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_claim_job(worker text) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_claim_job(worker text) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_claim_job(worker text) TO rif_probe'; END IF; END $do$",
    "ALTER TABLE links DROP CONSTRAINT IF EXISTS links_kind_known",
    "ALTER TABLE links ADD CONSTRAINT links_kind_known CHECK (kind IN ('references', 'supersedes', 'contradicts', 'derived_from', 'about', 'part_of'))",
    "ALTER TABLE links DROP CONSTRAINT IF EXISTS links_source_known",
    "ALTER TABLE links ADD CONSTRAINT links_source_known CHECK (source IN ('author', 'wikilink', 'dream', 'import'))",
    "ALTER TABLE links DROP CONSTRAINT IF EXISTS links_status_known",
    "ALTER TABLE links ADD CONSTRAINT links_status_known CHECK (status IN ('active', 'proposed', 'rejected', 'superseded'))",
    "ALTER TABLE links DROP CONSTRAINT IF EXISTS links_not_to_itself",
    "ALTER TABLE links ADD CONSTRAINT links_not_to_itself CHECK (from_engram_id <> to_engram_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS links_one_active ON links (from_engram_id, to_engram_id, kind) WHERE status = 'active'",
    "CREATE INDEX IF NOT EXISTS links_inbound ON links (to_engram_id) WHERE status = 'active'",
    "ALTER TABLE links ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE links FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS links_select ON links",
    "DROP POLICY IF EXISTS links_insert ON links",
    "CREATE POLICY links_select ON links FOR SELECT USING (from_engram_id IN (SELECT id FROM engrams) AND to_engram_id IN (SELECT id FROM engrams))",
    "CREATE POLICY links_insert ON links FOR INSERT WITH CHECK (from_engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids())) AND to_engram_id IN (SELECT id FROM engrams) AND author_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND (NOT EXISTS (SELECT 1 FROM engrams t JOIN spaces c ON c.id = t.space_id WHERE t.id = links.to_engram_id AND c.kind = 'personal') OR EXISTS (SELECT 1 FROM engrams f JOIN spaces c ON c.id = f.space_id WHERE f.id = links.from_engram_id AND c.kind = 'personal')))",
    """CREATE OR REPLACE FUNCTION reef_retire_links(p_engram uuid) RETURNS void LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
    UPDATE links l SET status = 'superseded', superseded_at = LOCALTIMESTAMP
    FROM engrams p
    WHERE p.id = p_engram
      AND l.from_engram_id = p.id
      AND p.space_id IN (
          SELECT space_id FROM memberships
          WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'
      )
      AND l.source = 'wikilink'
      AND l.status = 'active'
      AND NOT (l.target_text = ANY (COALESCE(p.reference_targets, '{}')))
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_retire_links(p_engram uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_retire_links(p_engram uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON engrams TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON links TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_links(p_engram uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_links(p_engram uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_links(p_engram uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_links(p_engram uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_links(p_engram uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_links(p_engram uuid) TO rif_probe'; END IF; END $do$",
    "ALTER TABLE engram_access DROP CONSTRAINT IF EXISTS engram_access_via_known",
    "ALTER TABLE engram_access ADD CONSTRAINT engram_access_via_known CHECK (via IN ('read', 'write', 'remember', 'web'))",
    "ALTER TABLE engram_access DROP CONSTRAINT IF EXISTS engram_access_weight_sane",
    "ALTER TABLE engram_access ADD CONSTRAINT engram_access_weight_sane CHECK (weight > 0 AND weight <= 10)",
    "CREATE INDEX IF NOT EXISTS engram_access_recent ON engram_access (person_id, engram_id, accessed_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS engram_heat_person_engram ON engram_heat (person_id, engram_id)",
    "ALTER TABLE engram_access ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE engram_access FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS engram_access_select ON engram_access",
    "DROP POLICY IF EXISTS engram_access_insert ON engram_access",
    "DROP POLICY IF EXISTS engram_access_delete ON engram_access",
    "CREATE POLICY engram_access_select ON engram_access FOR SELECT USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND engram_id IN (SELECT id FROM engrams))",
    "CREATE POLICY engram_access_insert ON engram_access FOR INSERT WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND engram_id IN (SELECT id FROM engrams))",
    "CREATE POLICY engram_access_delete ON engram_access FOR DELETE USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "ALTER TABLE engram_heat ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE engram_heat FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS engram_heat_owner ON engram_heat",
    "CREATE POLICY engram_heat_owner ON engram_heat USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND engram_id IN (SELECT id FROM engrams)) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND engram_id IN (SELECT id FROM engrams))",
    "ALTER TABLE search_log DROP CONSTRAINT IF EXISTS search_log_query_sane",
    "ALTER TABLE search_log ADD CONSTRAINT search_log_query_sane CHECK (char_length(query) BETWEEN 1 AND 500)",
    "ALTER TABLE search_log DROP CONSTRAINT IF EXISTS search_log_results_sane",
    "ALTER TABLE search_log ADD CONSTRAINT search_log_results_sane CHECK (cardinality(engram_ids) <= 50)",
    "CREATE INDEX IF NOT EXISTS search_log_recent ON search_log (person_id, searched_at DESC)",
    "CREATE INDEX IF NOT EXISTS search_log_age ON search_log (searched_at)",
    "ALTER TABLE search_log ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE search_log FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS search_log_select ON search_log",
    "DROP POLICY IF EXISTS search_log_insert ON search_log",
    "DROP POLICY IF EXISTS search_log_delete ON search_log",
    "CREATE POLICY search_log_select ON search_log FOR SELECT USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY search_log_insert ON search_log FOR INSERT WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY search_log_delete ON search_log FOR DELETE USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    """CREATE OR REPLACE FUNCTION reef_purge_search_log() RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
    removed integer;
BEGIN
    DELETE FROM search_log
    WHERE searched_at < LOCALTIMESTAMP - make_interval(days => 90);
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_purge_search_log() OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_purge_search_log() FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON search_log TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_search_log() TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_search_log() TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_search_log() TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_search_log() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_search_log() TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_search_log() TO rif_probe'; END IF; END $do$",
    "ALTER TABLE engram_embeddings DROP CONSTRAINT IF EXISTS engram_embeddings_dims",
    "ALTER TABLE engram_embeddings ADD CONSTRAINT engram_embeddings_dims CHECK (dims = vector_dims(embedding) AND dims BETWEEN 1 AND 4096)",
    "CREATE UNIQUE INDEX IF NOT EXISTS engram_embeddings_piece ON engram_embeddings (engram_id, model_id, COALESCE(section_anchor, ''))",
    "ALTER TABLE engram_embeddings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE engram_embeddings FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS engram_embeddings_select ON engram_embeddings",
    "DROP POLICY IF EXISTS engram_embeddings_insert ON engram_embeddings",
    "DROP POLICY IF EXISTS engram_embeddings_delete ON engram_embeddings",
    "CREATE POLICY engram_embeddings_select ON engram_embeddings FOR SELECT USING (engram_id IN (SELECT id FROM engrams))",
    "CREATE POLICY engram_embeddings_insert ON engram_embeddings FOR INSERT WITH CHECK (engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE POLICY engram_embeddings_delete ON engram_embeddings FOR DELETE USING (engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE UNIQUE INDEX IF NOT EXISTS embedding_spend_person_day ON embedding_spend (person_id, day)",
    "ALTER TABLE embedding_spend DROP CONSTRAINT IF EXISTS embedding_spend_sane",
    "ALTER TABLE embedding_spend ADD CONSTRAINT embedding_spend_sane CHECK (pieces >= 0)",
    "ALTER TABLE embedding_spend ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE embedding_spend FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS embedding_spend_owner ON embedding_spend",
    "CREATE POLICY embedding_spend_owner ON embedding_spend USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE OR REPLACE FUNCTION reef_person_session_epoch(p_id uuid) RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT session_epoch FROM persons WHERE id = p_id$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_person_session_epoch(p_id uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_person_session_epoch(p_id uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_session_epoch(p_id uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_session_epoch(p_id uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_session_epoch(p_id uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_session_epoch(p_id uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_session_epoch(p_id uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_person_session_epoch(p_id uuid) TO rif_probe'; END IF; END $do$",
    "DROP FUNCTION IF EXISTS reef_admit_member(uuid, uuid, text)",
    """CREATE OR REPLACE FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
  candidate text;
  suffix int := 1;
BEGIN
  -- Only the space's owner admits anybody, matching memberships_insert.
  IF NOT EXISTS (SELECT 1 FROM spaces WHERE id = p_space
                 AND owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) THEN
    RETURN NULL;
  END IF;
  -- The role is decided here, where the row is written, so no caller can
  -- smuggle in a third value the write policies have never heard of.
  IF p_role NOT IN ('member', 'viewer') THEN
    RETURN NULL;
  END IF;
  IF EXISTS (SELECT 1 FROM memberships WHERE space_id = p_space
             AND person_id = p_person) THEN
    RETURN NULL;
  END IF;
  -- The alias has to be free for the *invitee*, and the owner cannot see
  -- the invitee's other memberships -- persons are self-only and
  -- memberships are visible per space, so evaluated in Python this check
  -- would come back clear and the insert would hit the unique constraint.
  -- In here it is both correct and atomic: no window between choosing a
  -- name and taking it.
  --
  -- 'personal' is refused outright rather than suffixed. It is the one
  -- alias whose meaning is fixed for everybody, and a space admitted under
  -- it would shadow the private space in every later call.
  candidate := p_alias;
  IF candidate = 'personal' OR candidate IS NULL OR candidate = '' THEN
    candidate := 'space';
  END IF;
  WHILE EXISTS (SELECT 1 FROM memberships WHERE person_id = p_person
                AND alias = candidate) LOOP
    suffix := suffix + 1;
    candidate := p_alias || '-' || suffix;
    -- A person with thousands of spaces is not a case worth looping over.
    IF suffix > 999 THEN
      RETURN NULL;
    END IF;
  END LOOP;
  INSERT INTO memberships (person_id, space_id, role, alias)
  VALUES (p_person, p_space, p_role, candidate);
  RETURN candidate;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON spaces TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON memberships TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text) TO rif_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'REVOKE UPDATE ON memberships FROM reef_app'; EXECUTE 'GRANT UPDATE (alias) ON memberships TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'REVOKE UPDATE ON memberships FROM reef'; EXECUTE 'GRANT UPDATE (alias) ON memberships TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'REVOKE UPDATE ON memberships FROM reef_probe'; EXECUTE 'GRANT UPDATE (alias) ON memberships TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'REVOKE UPDATE ON memberships FROM rif_app'; EXECUTE 'GRANT UPDATE (alias) ON memberships TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'REVOKE UPDATE ON memberships FROM rif'; EXECUTE 'GRANT UPDATE (alias) ON memberships TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'REVOKE UPDATE ON memberships FROM rif_probe'; EXECUTE 'GRANT UPDATE (alias) ON memberships TO rif_probe'; END IF; END $do$",
    """CREATE OR REPLACE FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) RETURNS TABLE(person_id uuid, person_email text, person_display_name text) LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
  bound_id uuid;
  bound_email text;
  bound_name text;
  taken int;
BEGIN
  -- Serialises admissions. The ceiling is a global count, so the check and
  -- the insert have to be uninterleaved or two callers racing for the last
  -- seat both see it free and both land -- the same check-then-act
  -- reef_allowlist_person locks the inviter's row to avoid. There is no
  -- per-caller row to lock here, the counted set being every open-door row
  -- at once, so the lock is advisory and taken on a fixed key. It releases
  -- when the transaction ends, however it ends.
  PERFORM pg_advisory_xact_lock(231738832);

  -- An address already on the allowlist is somebody a member invited for
  -- real, possibly in the seconds between the callback and the click.
  -- Binding it is right; spending a launch seat on it is not, because the
  -- ceiling exists to bound strangers.
  --
  -- ``subject IS NULL`` is the same rule reef_person_bind enforces, and it
  -- is load-bearing rather than tidy: without it, anybody able to prove
  -- control of an address a member already signed in with would be handed
  -- that member's account.
  UPDATE persons SET subject = p_subject
   WHERE email = lower(p_email) AND subject IS NULL
  RETURNING id, email, display_name INTO bound_id, bound_email, bound_name;
  IF FOUND THEN
    -- Cast explicitly: the columns are varchar(255) and the signature says
    -- text, which RETURN QUERY compares strictly rather than coercing.
    RETURN QUERY SELECT bound_id, bound_email::text, bound_name::text;
    RETURN;
  END IF;

  -- Nothing was bound: either no such row, or one whose subject is already
  -- set. The second case must not fall through to the insert -- the unique
  -- index on email would refuse it anyway, but as a raw driver error rather
  -- than the refusal the caller is written to expect.
  IF EXISTS (SELECT 1 FROM persons WHERE email = lower(p_email)) THEN
    RETURN;
  END IF;

  SELECT count(*) INTO taken FROM persons WHERE joined_open_door;
  IF taken >= p_seats THEN
    RETURN;
  END IF;

  RETURN QUERY
  INSERT INTO persons (id, email, subject, display_name, joined_open_door,
                       created_at)
  VALUES (gen_random_uuid(), lower(p_email), p_subject, p_display_name, true,
          LOCALTIMESTAMP)
  RETURNING id, email::text, display_name::text;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON persons TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_open_door_admit(p_email text, p_subject text, p_display_name text, p_seats int) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_member_faces(p_space uuid) RETURNS TABLE(person_id uuid, avatar_len int) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.id, octet_length(p.avatar_bytes)::int FROM persons p WHERE p.id IN (SELECT person_id FROM memberships WHERE space_id = p_space) AND p.avatar_bytes IS NOT NULL AND p_space IN (SELECT space_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_member_faces(p_space uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_member_faces(p_space uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_space uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_space uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_space uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_space uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_space uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_space uuid) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_member_avatar(p_space uuid, p_person uuid) RETURNS TABLE(avatar_mime text, avatar_bytes bytea) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.avatar_mime, p.avatar_bytes FROM persons p WHERE p.id = p_person AND p.id IN (SELECT person_id FROM memberships WHERE space_id = p_space) AND p.avatar_bytes IS NOT NULL AND p_space IN (SELECT space_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_member_avatar(p_space uuid, p_person uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_member_avatar(p_space uuid, p_person uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_space uuid, p_person uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_space uuid, p_person uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_space uuid, p_person uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_space uuid, p_person uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_space uuid, p_person uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_space uuid, p_person uuid) TO rif_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'REVOKE UPDATE ON persons FROM reef_app'; EXECUTE 'GRANT UPDATE (display_name, avatar_mime, avatar_bytes, session_epoch) ON persons TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'REVOKE UPDATE ON persons FROM reef'; EXECUTE 'GRANT UPDATE (display_name, avatar_mime, avatar_bytes, session_epoch) ON persons TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'REVOKE UPDATE ON persons FROM reef_probe'; EXECUTE 'GRANT UPDATE (display_name, avatar_mime, avatar_bytes, session_epoch) ON persons TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'REVOKE UPDATE ON persons FROM rif_app'; EXECUTE 'GRANT UPDATE (display_name, avatar_mime, avatar_bytes, session_epoch) ON persons TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'REVOKE UPDATE ON persons FROM rif'; EXECUTE 'GRANT UPDATE (display_name, avatar_mime, avatar_bytes, session_epoch) ON persons TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'REVOKE UPDATE ON persons FROM rif_probe'; EXECUTE 'GRANT UPDATE (display_name, avatar_mime, avatar_bytes, session_epoch) ON persons TO rif_probe'; END IF; END $do$",
]


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
                *REFRESH,
            ]
        )

    manager.add_raw(run)
    return manager
