"""Log each person's calls and their reasons privately, and record who changed an engram."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-20T12:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "per-person call log, and the writer kind on revisions"

# The statements in CALL_LOG_STATEMENTS were printed from
# reef.rls.call_log_statements() and are fixed text. This module never imports
# reef.rls: a migration must run the same SQL on the day it is applied as on
# the day it was written, however the live DDL has moved since.
# tests/test_migration_chain.py compares the migrated schema with the one the
# suite builds from reef.rls, so the two cannot drift unseen.

_EXECUTOR_ROLES = ("reef_app", "reef", "reef_probe", "rif_app", "rif", "rif_probe")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS call_log (
    id UUID PRIMARY KEY,
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    session_id VARCHAR(255) DEFAULT NULL,
    method VARCHAR NOT NULL,
    reason VARCHAR NOT NULL DEFAULT '',
    writer VARCHAR NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    returned INTEGER NOT NULL DEFAULT 0,
    outcome VARCHAR NOT NULL,
    called_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP
)
"""

ADD_CHANNEL = (
    "ALTER TABLE revisions ADD COLUMN IF NOT EXISTS channel VARCHAR DEFAULT NULL"
)

CALL_LOG_STATEMENTS = [
    "ALTER TABLE call_log DROP CONSTRAINT IF EXISTS call_log_reason_sane",
    "ALTER TABLE call_log ADD CONSTRAINT call_log_reason_sane CHECK (char_length(reason) <= 200)",
    "ALTER TABLE call_log DROP CONSTRAINT IF EXISTS call_log_writer_known",
    "ALTER TABLE call_log ADD CONSTRAINT call_log_writer_known CHECK (writer IN ('assistant', 'person', 'system'))",
    "ALTER TABLE revisions DROP CONSTRAINT IF EXISTS revisions_channel_known",
    "ALTER TABLE revisions ADD CONSTRAINT revisions_channel_known CHECK (channel IS NULL OR channel IN ('assistant', 'person', 'system'))",
    "CREATE INDEX IF NOT EXISTS call_log_recent ON call_log (person_id, called_at DESC)",
    "CREATE INDEX IF NOT EXISTS call_log_age ON call_log (called_at)",
    "ALTER TABLE call_log ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE call_log FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS call_log_select ON call_log",
    "DROP POLICY IF EXISTS call_log_insert ON call_log",
    "CREATE POLICY call_log_select ON call_log FOR SELECT USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    "CREATE POLICY call_log_insert ON call_log FOR INSERT WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
    """CREATE OR REPLACE FUNCTION reef_purge_call_log() RETURNS integer LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$
DECLARE
    removed integer;
BEGIN
    DELETE FROM call_log
    WHERE called_at < LOCALTIMESTAMP - make_interval(days => 90);
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$reef$""",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_purge_call_log() OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_purge_call_log() FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON call_log TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_call_log() TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_call_log() TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_call_log() TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_call_log() TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_call_log() TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_purge_call_log() TO rif_probe'; END IF; END $do$",
]


async def forwards() -> MigrationManager:
    """Create ``call_log`` with grants, policies and its purge; add ``revisions.channel``.

    The key cascades: a person who deletes their account takes their call log
    with them. The roles get ``SELECT`` and ``INSERT`` only: a logged call is
    never rewritten or deleted by the app. Old revisions keep a null channel.

    The table starts empty, and stays empty until the operator switches
    logging on with ``REEF_CALL_LOG``.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        grants = [
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'GRANT SELECT, INSERT "
            f"ON call_log TO {role}'; END IF; END $$"
            for role in _EXECUTOR_ROLES
        ]
        await run_ddl_atomically(
            [CREATE_TABLE, ADD_CHANNEL, *grants, *CALL_LOG_STATEMENTS]
        )

    manager.add_raw(run)
    return manager
