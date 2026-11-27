"""Require target write access for replaces and allow retiring visible author links.

Existing rows stay unchanged. The insert policy now requires membership with
write access at both ends for supersedes; other kinds keep their rights.
The new helper retires only active author links the caller can read, and
only when the caller may change their source. It returns no row or count.
Its owner and grants accept both reef and former rif role names; its search
path is pg_catalog, public, pg_temp. Fixed SQL runs atomically, safely twice.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-26T15:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "enforce and retire typed links"

STATEMENTS = [
    "DROP POLICY IF EXISTS links_insert ON links",
    "CREATE POLICY links_insert ON links FOR INSERT WITH CHECK (from_engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids())) AND to_engram_id IN (SELECT id FROM engrams) AND (kind <> 'supersedes' OR to_engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids()))) AND author_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND (NOT EXISTS (SELECT 1 FROM engrams t JOIN spaces c ON c.id = t.space_id WHERE t.id = links.to_engram_id AND c.kind = 'personal') OR EXISTS (SELECT 1 FROM engrams f JOIN spaces c ON c.id = f.space_id WHERE f.id = links.from_engram_id AND c.kind = 'personal')))",
    "CREATE OR REPLACE FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) RETURNS void LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $reef$\n    UPDATE links l SET status = 'superseded', superseded_at = LOCALTIMESTAMP\n    FROM engrams p\n    WHERE p.id = p_engram\n      AND l.from_engram_id = p.id\n      AND p.space_id IN (\n          SELECT space_id FROM memberships\n          WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'\n      )\n      AND l.source = 'author'\n      AND l.kind = p_kind\n      AND l.status = 'active'\n      AND NOT (l.to_engram_id = ANY (COALESCE(p_keep, '{}'::uuid[])))\n      AND l.to_engram_id IN (\n          SELECT e.id FROM engrams e JOIN memberships m ON m.space_id = e.space_id\n          WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid\n      )\n$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON engrams TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON links TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[]) TO rif_probe'; END IF; END $do$",
]


async def forwards() -> MigrationManager:
    """Apply the typed-link rules.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Apply the fixed statements in one transaction."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
