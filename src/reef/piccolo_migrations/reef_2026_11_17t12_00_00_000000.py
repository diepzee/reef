"""Add the job queue: background work, done as the person it is for."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.rls import job_statements

ID = "2026-11-17T12:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "job queue under row security, with a claim-only function"

_EXECUTOR_ROLES = ("reef_app", "reef", "reef_probe", "rif_app", "rif", "rif_probe")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY,
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    kind VARCHAR(255) NOT NULL,
    subject_ids UUID[] NOT NULL DEFAULT '{}',
    status VARCHAR(255) NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    run_after TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP,
    locked_by VARCHAR(255) DEFAULT NULL,
    locked_at TIMESTAMP DEFAULT NULL,
    error VARCHAR(255) DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP,
    finished_at TIMESTAMP DEFAULT NULL
)
"""


async def forwards() -> MigrationManager:
    """Create ``jobs`` with its grants, policy and claim function.

    The foreign key cascades: a person who deletes their account takes
    their queued work with them. Nothing here is worth keeping without them,
    and an orphan row would be unreachable under the policy anyway.

    One transaction, so the table never exists with row security enabled
    and no policy on it -- which would deny every row to everyone -- nor
    with grants and no row security at all.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        grants = [
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON jobs TO {role}'; END IF; END $$"
            for role in _EXECUTOR_ROLES
        ]
        await run_ddl_atomically([CREATE_TABLE, *grants, *job_statements()])

    manager.add_raw(run)
    return manager
