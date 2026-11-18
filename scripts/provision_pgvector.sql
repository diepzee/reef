-- One-time provisioning of the pgvector extension. Run as a superuser.
--
-- The extension is not marked "trusted", so only a superuser may create it,
-- and neither the app role nor the migration role is one -- deliberately.
-- Migrations assume the extension exists and refuse to run without it.
--
-- Idempotent, and it touches nothing else. See docs/runbook.md, "pgvector".
--
-- Check first that the server ships it at all. No row here means the
-- Postgres image has no pgvector, and CREATE EXTENSION below will fail:
SELECT name, default_version, installed_version
FROM pg_available_extensions
WHERE name = 'vector';

CREATE EXTENSION IF NOT EXISTS vector;
