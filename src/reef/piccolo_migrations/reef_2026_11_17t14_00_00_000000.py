"""Record how each person uses their pages, and what that makes active."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.rls import usage_statements

ID = "2026-11-17T14:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "per-person usage log and activation"

_EXECUTOR_ROLES = ("reef_app", "reef", "reef_probe", "rif_app", "rif", "rif_probe")

CREATE_ACCESS = """
CREATE TABLE IF NOT EXISTS page_access (
    id UUID PRIMARY KEY,
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    via VARCHAR(255) NOT NULL DEFAULT 'read',
    weight REAL NOT NULL DEFAULT 1.0,
    session_id VARCHAR(255) DEFAULT NULL,
    accessed_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP
)
"""

CREATE_HEAT = """
CREATE TABLE IF NOT EXISTS page_heat (
    id UUID PRIMARY KEY,
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    activation REAL NOT NULL,
    accesses INTEGER NOT NULL,
    last_access TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP
)
"""


async def forwards() -> MigrationManager:
    """Create ``page_access`` and ``page_heat`` with grants and policies.

    Every key cascades. A person who deletes their account takes their
    usage log and activation with them, which is what account deletion
    promises. A deleted page takes the record of its use.

    Both tables start empty. There is nothing to backfill: no use was
    recorded before this.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        grants = [
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON {table} TO {role}'; END IF; END $$"
            for role in _EXECUTOR_ROLES
            for table in ("page_access", "page_heat")
        ]
        await run_ddl_atomically(
            [CREATE_ACCESS, CREATE_HEAT, *grants, *usage_statements()]
        )

    manager.add_raw(run)
    return manager
