"""Store the vectors of page pieces, and count what embedding costs."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.frozen_ddl import embedding_statements

ID = "2026-11-18T12:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "page embeddings and the daily embedding spend"

_EXECUTOR_ROLES = ("reef_app", "reef", "reef_probe", "rif_app", "rif", "rif_probe")

CREATE_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS page_embeddings (
    id UUID PRIMARY KEY,
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    section_anchor VARCHAR DEFAULT NULL,
    model_id VARCHAR(255) NOT NULL,
    dims INTEGER NOT NULL,
    content_hash VARCHAR(255) NOT NULL,
    embedding VECTOR NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP
)
"""

CREATE_SPEND = """
CREATE TABLE IF NOT EXISTS embedding_spend (
    id UUID PRIMARY KEY,
    person_id UUID NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    day DATE NOT NULL,
    pieces INTEGER NOT NULL DEFAULT 0
)
"""

GRANTS = {
    # No UPDATE: a vector is replaced by deleting it and adding the new one.
    "page_embeddings": "SELECT, INSERT, DELETE",
    "embedding_spend": "SELECT, INSERT, UPDATE, DELETE",
}


async def forwards() -> MigrationManager:
    """Create ``page_embeddings`` and ``embedding_spend`` with their policies.

    Needs the ``vector`` extension. A migration cannot create it: that takes
    a superuser, once. See ``scripts/provision_pgvector.sql``.

    Both tables start empty and stay empty until an embedding service is
    configured. See ``REEF_EMBEDDING_URL`` in the runbook.

    Every key cascades. A deleted page takes its vectors, and a deleted
    account takes its spend.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        grants = [
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'GRANT {privileges} "
            f"ON {table} TO {role}'; END IF; END $$"
            for role in _EXECUTOR_ROLES
            for table, privileges in GRANTS.items()
        ]
        await run_ddl_atomically(
            [CREATE_EMBEDDINGS, CREATE_SPEND, *grants, *embedding_statements()]
        )

    manager.add_raw(run)
    return manager
