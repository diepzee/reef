"""Keep the text Reef reads from stored files, under row security.

Creates ``file_pages`` (one row per page of a file, with an English and Dutch
search vector), ``file_embeddings`` (one vector per chunk of a file and model)
and ``reading_spend`` (pages and tokens read per person, day and reader), and
adds ``text_status``, ``pages`` and ``text_error`` to ``attachments``.

Nothing writes to them until ``REEF_FILE_TEXT`` is switched on, so this
changes no behaviour on its own.

The SQL is fixed text: :data:`STATEMENTS` was printed from
``reef.rls.file_text_statements()`` and :data:`SEARCH_VECTOR` from
``reef.search.search_statements()`` on the day this was written. This module
never imports ``reef.rls``: a migration must run the same SQL on the day it is
applied as on the day it was written. ``tests/test_migration_chain.py``
checks that what the chain builds is what the suite builds.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-21T12:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "file pages, file chunk vectors and reading spend"

_EXECUTOR_ROLES = ("reef_app", "reef", "reef_probe", "rif_app", "rif", "rif_probe")

#: As Piccolo creates them from ``reef.models``.
CREATE_TABLES = [
    """
CREATE TABLE IF NOT EXISTS file_pages (
    id UUID PRIMARY KEY NOT NULL DEFAULT gen_random_uuid(),
    attachment_id UUID NOT NULL REFERENCES attachments (id) ON DELETE CASCADE ON UPDATE CASCADE DEFAULT null,
    number INTEGER NOT NULL DEFAULT 0,
    text VARCHAR NOT NULL DEFAULT '',
    reader VARCHAR(255) NOT NULL DEFAULT '',
    model VARCHAR(255) DEFAULT null,
    confidence REAL DEFAULT null,
    read_at TIMESTAMP NOT NULL DEFAULT current_timestamp
)
""",
    """
CREATE TABLE IF NOT EXISTS file_embeddings (
    id UUID PRIMARY KEY NOT NULL DEFAULT gen_random_uuid(),
    attachment_id UUID NOT NULL REFERENCES attachments (id) ON DELETE CASCADE ON UPDATE CASCADE DEFAULT null,
    chunk_anchor VARCHAR DEFAULT null,
    model_id VARCHAR(255) NOT NULL DEFAULT '',
    dims INTEGER NOT NULL DEFAULT 0,
    content_hash VARCHAR(255) NOT NULL DEFAULT '',
    embedding VECTOR NOT NULL DEFAULT null,
    created_at TIMESTAMP NOT NULL DEFAULT current_timestamp
)
""",
    """
CREATE TABLE IF NOT EXISTS reading_spend (
    id UUID PRIMARY KEY NOT NULL DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES persons (id) ON DELETE CASCADE ON UPDATE CASCADE DEFAULT null,
    day DATE NOT NULL DEFAULT CURRENT_DATE,
    reader VARCHAR(255) NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
)
""",
]

ADD_COLUMNS = [
    "ALTER TABLE attachments ADD COLUMN IF NOT EXISTS text_status VARCHAR(255) DEFAULT null",
    "ALTER TABLE attachments ADD COLUMN IF NOT EXISTS pages INTEGER DEFAULT null",
    "ALTER TABLE attachments ADD COLUMN IF NOT EXISTS text_error VARCHAR(255) DEFAULT null",
]

GRANTS = {
    "file_pages": "SELECT, INSERT, UPDATE, DELETE",
    # No UPDATE: a vector is replaced by deleting it and adding the new one.
    "file_embeddings": "SELECT, INSERT, DELETE",
    "reading_spend": "SELECT, INSERT, UPDATE, DELETE",
}

#: Run in order, after the tables and grants. Each can run again.
STATEMENTS = [
    "ALTER TABLE attachments DROP CONSTRAINT IF EXISTS attachments_text_status_known",
    "ALTER TABLE attachments ADD CONSTRAINT attachments_text_status_known CHECK (text_status IS NULL OR text_status IN ('waiting', 'read', 'partly_read', 'unreadable', 'not_read'))",
    "ALTER TABLE file_pages DROP CONSTRAINT IF EXISTS file_pages_reader_known",
    "ALTER TABLE file_pages ADD CONSTRAINT file_pages_reader_known CHECK (reader IN ('pdf-text', 'tesseract', 'markdown', 'plain', 'html', 'docx', 'xlsx', 'pptx'))",
    "ALTER TABLE file_pages DROP CONSTRAINT IF EXISTS file_pages_sane",
    "ALTER TABLE file_pages ADD CONSTRAINT file_pages_sane CHECK (number >= 1 AND (confidence IS NULL OR confidence BETWEEN 0 AND 100))",
    "CREATE UNIQUE INDEX IF NOT EXISTS file_pages_number ON file_pages (attachment_id, number)",
    "ALTER TABLE file_pages ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE file_pages FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS file_pages_select ON file_pages",
    "DROP POLICY IF EXISTS file_pages_insert ON file_pages",
    "DROP POLICY IF EXISTS file_pages_update ON file_pages",
    "DROP POLICY IF EXISTS file_pages_delete ON file_pages",
    "CREATE POLICY file_pages_select ON file_pages FOR SELECT USING (attachment_id IN (SELECT id FROM attachments))",
    "CREATE POLICY file_pages_insert ON file_pages FOR INSERT WITH CHECK (attachment_id IN (SELECT id FROM attachments WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE POLICY file_pages_update ON file_pages FOR UPDATE USING (attachment_id IN (SELECT id FROM attachments WHERE space_id IN (SELECT reef_member_space_ids()))) WITH CHECK (attachment_id IN (SELECT id FROM attachments WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE POLICY file_pages_delete ON file_pages FOR DELETE USING (attachment_id IN (SELECT id FROM attachments WHERE space_id IN (SELECT reef_member_space_ids())))",
    "ALTER TABLE file_embeddings DROP CONSTRAINT IF EXISTS file_embeddings_dims",
    "ALTER TABLE file_embeddings ADD CONSTRAINT file_embeddings_dims CHECK (dims = vector_dims(embedding) AND dims BETWEEN 1 AND 4096)",
    "CREATE UNIQUE INDEX IF NOT EXISTS file_embeddings_chunk ON file_embeddings (attachment_id, model_id, COALESCE(chunk_anchor, ''))",
    "ALTER TABLE file_embeddings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE file_embeddings FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS file_embeddings_select ON file_embeddings",
    "DROP POLICY IF EXISTS file_embeddings_insert ON file_embeddings",
    "DROP POLICY IF EXISTS file_embeddings_delete ON file_embeddings",
    "CREATE POLICY file_embeddings_select ON file_embeddings FOR SELECT USING (attachment_id IN (SELECT id FROM attachments))",
    "CREATE POLICY file_embeddings_insert ON file_embeddings FOR INSERT WITH CHECK (attachment_id IN (SELECT id FROM attachments WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE POLICY file_embeddings_delete ON file_embeddings FOR DELETE USING (attachment_id IN (SELECT id FROM attachments WHERE space_id IN (SELECT reef_member_space_ids())))",
    "CREATE UNIQUE INDEX IF NOT EXISTS reading_spend_person_day_reader ON reading_spend (person_id, day, reader)",
    "ALTER TABLE reading_spend DROP CONSTRAINT IF EXISTS reading_spend_sane",
    "ALTER TABLE reading_spend ADD CONSTRAINT reading_spend_sane CHECK (pages >= 0 AND input_tokens >= 0 AND output_tokens >= 0)",
    "ALTER TABLE reading_spend ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE reading_spend FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS reading_spend_owner ON reading_spend",
    "CREATE POLICY reading_spend_owner ON reading_spend USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
]

#: The ``file_pages`` line of ``reef.search.search_statements()``.
SEARCH_VECTOR = "ALTER TABLE file_pages ADD COLUMN IF NOT EXISTS search_vector tsvector GENERATED ALWAYS AS (setweight(to_tsvector('english', coalesce(NULL, '')), 'A') || setweight(to_tsvector('english', left(coalesce(text, ''), 200000)), 'B') || setweight(to_tsvector('dutch', coalesce(NULL, '')), 'A') || setweight(to_tsvector('dutch', left(coalesce(text, ''), 200000)), 'B')) STORED"


async def forwards() -> MigrationManager:
    """Create the file text tables with grants, checks and policies.

    Needs the ``vector`` extension, which ``2026-11-18T12:00:00`` already
    needed. Every key cascades: a deleted file takes its pages and vectors,
    and a deleted account takes its spend.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Create, grant and arm the tables, in one transaction."""
        grants = [
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'GRANT {privileges} "
            f"ON {table} TO {role}'; END IF; END $$"
            for role in _EXECUTOR_ROLES
            for table, privileges in GRANTS.items()
        ]
        await run_ddl_atomically(
            [*CREATE_TABLES, *ADD_COLUMNS, *grants, *STATEMENTS, SEARCH_VECTOR]
        )

    manager.add_raw(run)
    return manager
