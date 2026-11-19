"""Store links between pages, so "what points here" is a query."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.frozen_ddl import link_statements

ID = "2026-11-17T13:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "link table, visible only when both ends are readable"

_EXECUTOR_ROLES = ("reef_app", "reef", "reef_probe", "rif_app", "rif", "rif_probe")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS links (
    id UUID PRIMARY KEY,
    from_page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    to_page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    from_anchor VARCHAR(255) DEFAULT NULL,
    to_anchor VARCHAR(255) DEFAULT NULL,
    kind VARCHAR(255) NOT NULL DEFAULT 'references',
    source VARCHAR(255) NOT NULL DEFAULT 'wikilink',
    status VARCHAR(255) NOT NULL DEFAULT 'active',
    target_text VARCHAR DEFAULT NULL,
    confidence REAL DEFAULT NULL,
    valid_from TIMESTAMP DEFAULT NULL,
    valid_to TIMESTAMP DEFAULT NULL,
    recorded_at TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP,
    superseded_at TIMESTAMP DEFAULT NULL,
    author_id UUID DEFAULT NULL REFERENCES persons(id) ON DELETE SET NULL
)
"""

ADD_LINKED_VERSION = "ALTER TABLE pages ADD COLUMN IF NOT EXISTS linked_version INTEGER"


async def forwards() -> MigrationManager:
    """Create ``links`` with its grants, policies and retire function.

    Both page keys cascade: a link without one of its pages says nothing.
    The author key is set to null instead, so a link outlives the account
    of the person who wrote it, as a page does.

    ``pages.linked_version`` records which version of a page has its wiki
    links stored. It is added empty, for the reason the index-fields
    migration gives: the migration role can see no pages, so it cannot
    fill anything in. Existing pages get their links from a job that runs
    as each person.

    One transaction, so the table never exists unprotected.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        grants = [
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON links TO {role}'; END IF; END $$"
            for role in _EXECUTOR_ROLES
        ]
        await run_ddl_atomically(
            [CREATE_TABLE, ADD_LINKED_VERSION, *grants, *link_statements()]
        )

    manager.add_raw(run)
    return manager
