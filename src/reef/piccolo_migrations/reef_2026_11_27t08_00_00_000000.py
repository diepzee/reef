"""Record used write confirmations and remove them after one day.

Creates used_write_tokens with a UUID row key, a person foreign key that
cascades on account deletion, a SHA-256 token digest and a timestamptz use
time. Adds unique (person_id, token_digest) and used_at indexes. Enables
and forces row security with SELECT and INSERT policies for the caller's
own rows only; there are no UPDATE or DELETE policies.

Grants SELECT and INSERT to existing reef and former rif executor roles,
and revokes UPDATE and DELETE from non-owner executors. The table owner's
rights stay intact so foreign-key cascades can delete rows. Creates the
void, no-argument reef_purge_write_tokens() SECURITY DEFINER helper, owned
by reef_authz or rif_authz, with SELECT and DELETE on this table, no PUBLIC
execution, execution for existing executor roles, and a fixed search path
of pg_catalog, public, pg_temp. It deletes rows older than 24 hours.

Existing tables, rows, policies, functions, role attributes and memberships
are left alone. No token or content is copied or backfilled. Fixed SQL runs
atomically and can safely run twice, preserving any token rows already there.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-27T08:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "record used write confirmations"

STATEMENTS = [
    "CREATE TABLE IF NOT EXISTS used_write_tokens (id uuid PRIMARY KEY NOT NULL, person_id uuid NOT NULL REFERENCES persons(id) ON DELETE CASCADE, token_digest varchar(64) NOT NULL, used_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "CREATE UNIQUE INDEX IF NOT EXISTS used_write_tokens_person_digest ON used_write_tokens (person_id, token_digest)",
    "CREATE INDEX IF NOT EXISTS used_write_tokens_age ON used_write_tokens (used_at)",
    "ALTER TABLE used_write_tokens ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE used_write_tokens FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS used_write_tokens_select ON used_write_tokens",
    "DROP POLICY IF EXISTS used_write_tokens_insert ON used_write_tokens",
    "CREATE POLICY used_write_tokens_select ON used_write_tokens FOR SELECT USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY used_write_tokens_insert ON used_write_tokens FOR INSERT WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN IF 'reef_app' <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'used_write_tokens'::regclass)) THEN EXECUTE 'REVOKE UPDATE, DELETE ON used_write_tokens FROM reef_app'; END IF; EXECUTE 'GRANT SELECT, INSERT ON used_write_tokens TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN IF 'reef' <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'used_write_tokens'::regclass)) THEN EXECUTE 'REVOKE UPDATE, DELETE ON used_write_tokens FROM reef'; END IF; EXECUTE 'GRANT SELECT, INSERT ON used_write_tokens TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN IF 'reef_probe' <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'used_write_tokens'::regclass)) THEN EXECUTE 'REVOKE UPDATE, DELETE ON used_write_tokens FROM reef_probe'; END IF; EXECUTE 'GRANT SELECT, INSERT ON used_write_tokens TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN IF 'rif_app' <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'used_write_tokens'::regclass)) THEN EXECUTE 'REVOKE UPDATE, DELETE ON used_write_tokens FROM rif_app'; END IF; EXECUTE 'GRANT SELECT, INSERT ON used_write_tokens TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN IF 'rif' <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'used_write_tokens'::regclass)) THEN EXECUTE 'REVOKE UPDATE, DELETE ON used_write_tokens FROM rif'; END IF; EXECUTE 'GRANT SELECT, INSERT ON used_write_tokens TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN IF 'rif_probe' <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'used_write_tokens'::regclass)) THEN EXECUTE 'REVOKE UPDATE, DELETE ON used_write_tokens FROM rif_probe'; END IF; EXECUTE 'GRANT SELECT, INSERT ON used_write_tokens TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_purge_write_tokens() RETURNS void LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $reef$DELETE FROM used_write_tokens WHERE used_at < now() - interval '24 hours'$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_purge_write_tokens() OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_purge_write_tokens() FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON used_write_tokens TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_write_tokens() TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_write_tokens() TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_write_tokens() TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_write_tokens() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_write_tokens() TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_write_tokens() TO rif_probe'; END IF; END $do$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT DELETE ON used_write_tokens TO %I', authz); END $authz$",
]


async def forwards() -> MigrationManager:
    """Add the token table, private policies and purge in one transaction.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Apply the fixed statements atomically."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
