"""Log each person's searches, privately, for ninety days."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.rls import search_log_statements

ID = "2026-11-18T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "per-person search log"

_EXECUTOR_ROLES = ("reef_app", "reef", "reef_probe", "rif_app", "rif", "rif_probe")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS search_log (
    id UUID PRIMARY KEY,
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    query VARCHAR NOT NULL,
    page_ids UUID[] NOT NULL DEFAULT '{}',
    session_id VARCHAR(255) DEFAULT NULL,
    searched_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP
)
"""


async def forwards() -> MigrationManager:
    """Create ``search_log`` with grants, policies and its purge function.

    The key cascades: a person who deletes their account takes their search
    log with them. The roles get no ``UPDATE``: a logged search is never
    rewritten.

    The table starts empty, and stays empty until the operator switches
    logging on. See ``REEF_SEARCH_LOG`` in the runbook.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        grants = [
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'GRANT SELECT, INSERT, DELETE "
            f"ON search_log TO {role}'; END IF; END $$"
            for role in _EXECUTOR_ROLES
        ]
        await run_ddl_atomically([CREATE_TABLE, *grants, *search_log_statements()])

    manager.add_raw(run)
    return manager
