# reef — common tasks.
#
# `just` with no arguments lists everything. Start with `just setup`, which
# is safe to re-run.
#
# The database is the part worth knowing about. reef's privacy boundary is
# row-level security, and RLS is only enforced against an *ordinary* role: a
# superuser, or any role holding BYPASSRLS, ignores every policy. The local
# cluster therefore has three roles with different jobs — `reef` owns the
# databases, `rif_authz` owns the policy helper functions and nothing else,
# and `reef_probe` is a non-owner stand-in that the test suite uses to assert
# privileges honestly. They are created once, at first cluster bootstrap, by
# docker/initdb — which only runs on an empty volume. `just db-roles` repairs
# a cluster that predates them.

set dotenv-load := true

python := ".venv/bin/python"

# List available recipes.
default:
    @just --list --unsorted

# --- setup ----------------------------------------------------------------

# Install Python and frontend dependencies, and start the database.
setup: install db-up
    @echo "Waiting for Postgres…" && just _db-wait
    @just db-roles
    @echo
    @echo "Ready. 'just test' runs everything."

# Install Python (uv) and frontend (bun) dependencies.
install:
    uv sync
    cd frontend && bun install

# --- database -------------------------------------------------------------

# Start the local Postgres container.
db-up:
    docker compose up -d db

# Stop the database, keeping its data.
db-down:
    docker compose stop db

# Block until Postgres accepts connections.
_db-wait:
    #!/usr/bin/env bash
    set -euo pipefail
    for _ in $(seq 1 60); do
      if pg_isready -h localhost -p 5433 -q; then exit 0; fi
      sleep 1
    done
    echo "Postgres did not come up on localhost:5433" >&2
    exit 1

# docker/initdb runs only on a fresh volume, so a cluster created before
# these roles existed is missing them — and the test suite fails loudly
# rather than silently proving a shape production does not have. Idempotent.

# Create the authz and probe roles on a cluster that predates them.
db-roles:
    #!/usr/bin/env bash
    set -euo pipefail
    psql() { PGPASSWORD=postgres command psql -h localhost -p 5433 -U postgres "$@"; }
    # Rename an old cluster's authz role rather than making a second one.
    # It is NOLOGIN, so nothing connects as it and the rename cannot lock
    # anybody out; function ownership is recorded by OID and follows along.
    # Clusters that already say reef_authz, and brand new ones, skip this.
    psql -d postgres -v ON_ERROR_STOP=0 -q <<'SQL' 2>/dev/null || true
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_authz')
         AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_authz')
      THEN ALTER ROLE rif_authz RENAME TO reef_authz; END IF;
    END $$;
    CREATE ROLE reef_authz NOLOGIN BYPASSRLS;
    GRANT reef_authz TO reef;
    CREATE ROLE reef_probe WITH LOGIN PASSWORD 'probe' NOSUPERUSER NOBYPASSRLS
      NOCREATEDB NOCREATEROLE NOREPLICATION;
    SQL
    for db in reef reef_test; do
      psql -d "$db" -q <<'SQL'
    GRANT CREATE ON SCHEMA public TO reef_authz;
    GRANT USAGE ON SCHEMA public TO reef_probe;
    SQL
      psql -d "$db" -q -c "GRANT CONNECT ON DATABASE $db TO reef_probe" || true
      # Superuser-only, like the roles above. This fails on a cluster still
      # running the plain postgres:17 image, which has no pgvector to
      # create: recreate it with `just db-recreate`.
      psql -d "$db" -q -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector"
    done
    echo "roles present: reef_authz (NOLOGIN BYPASSRLS), reef_probe (ordinary)"
    echo "extension present: vector"

# A major-version or image change cannot reuse the old data directory, so the
# volume has to go. This is the one recipe here that deletes development data.

# Destroy the local cluster and its data, then bootstrap a fresh one.
db-recreate:
    docker compose down --volumes
    docker compose up -d db
    @echo "Waiting for Postgres…" && just _db-wait
    @echo "Fresh cluster. Run 'just migrate' to rebuild the development schema."

# Apply migrations to the development database.
migrate:
    {{python}} scripts/migrate.py

# Open a psql shell on the development database as the app role.
psql:
    PGPASSWORD=reef psql -h localhost -p 5433 -U reef -d reef

# Drop and rebuild the *test* database's schema. Development data is untouched.
db-reset-test:
    #!/usr/bin/env bash
    set -euo pipefail
    PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d reef_test -q <<'SQL'
    DROP SCHEMA public CASCADE;
    CREATE SCHEMA public;
    GRANT ALL ON SCHEMA public TO reef;
    -- The extension's objects lived in the schema that was just dropped, so
    -- the cascade took the extension with it.
    CREATE EXTENSION IF NOT EXISTS vector;
    GRANT USAGE ON SCHEMA public TO reef_probe;
    -- Both spellings of the authz role: it was renamed once, and a cluster
    -- may carry either. Granting only the old one leaves the new one unable
    -- to create its helper functions, which fails as "permission denied for
    -- schema public" long after the reset that caused it.
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_authz') THEN
        GRANT USAGE, CREATE ON SCHEMA public TO reef_authz;
      END IF;
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_authz') THEN
        GRANT USAGE, CREATE ON SCHEMA public TO rif_authz;
      END IF;
    END $$;
    SQL
    echo "reef_test schema reset; the next test run rebuilds it"

# --- tests and checks -----------------------------------------------------

# Everything CI would run: lint, format check, backend and frontend tests.
test: lint test-py test-js

# Every worktree shares one reef_test, and the schema fixture rebuilds the
# helper functions globally — so two suites at once clobber each other and
# the second reports a screenful of failures that are not about the code.
# scripts/run_tests.py holds a machine-wide lock so the second one waits.

# Rewrite the runbook's table of every setting from src/reef/config.py.
settings-table:
    {{python}} scripts/settings_table.py

# Run the Python test suite. Pass a path or -k expression: just test-py tests/test_rls.py
test-py *args:
    {{python}} scripts/run_tests.py {{args}}

# Run the Python suite in a fixed order, for reproducing an ordering bug.
test-py-ordered *args:
    {{python}} scripts/run_tests.py -p no:randomly {{args}}

# Run the frontend test suite.
test-js *args:
    cd frontend && bun test {{args}}

# Typecheck the frontend.
typecheck:
    cd frontend && bunx tsc --noEmit

# Lint and check formatting, changing nothing.
lint:
    {{python}} -m ruff check src tests scripts clients
    {{python}} -m ruff format --check src tests scripts clients

# Apply formatting and every safe lint fix.
fmt:
    {{python}} -m ruff check --fix src tests scripts clients
    {{python}} -m ruff format src tests scripts clients

# --- retrieval benchmark ----------------------------------------------------

# Local only. The scripts refuse any database that is not on this machine.
# See scripts/eval/README.md.

# Download the benchmark file (277 MB) and check it is the expected one.
eval-download:
    mkdir -p scripts/eval/data
    curl -sSL --fail -o scripts/eval/data/longmemeval_s_cleaned.json \
      "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json"
    {{python}} -c "import hashlib, sys; sys.path.insert(0, 'scripts/eval'); import evalkit; \
      d = hashlib.sha256(evalkit.DATA_FILE.read_bytes()).hexdigest(); \
      sys.exit(0 if d == evalkit.SOURCE_SHA256 else f'unexpected file: sha256 {d}')"

# Load benchmark sessions into the local database as the eval person's pages.
eval-seed questions="100":
    {{python}} scripts/eval/seed.py --questions {{questions}}

# Ask the benchmark questions in every search mode and print the scores.
eval questions="100":
    {{python}} scripts/eval/run.py --questions {{questions}}

# Create a local account of made-up facts, test@reef.local, to try things by hand.
test-account:
    {{python}} scripts/eval/seed.py --test-account

# Create or reset conversations@reef.local, the made-up person for test conversations.
conversations-account:
    {{python}} scripts/conversations/local_account.py

# Load your own JSON export as the private eval person's pages.
eval-private-seed:
    {{python}} scripts/eval/seed.py --private

# Ask the private set's questions of your own pages.
eval-private:
    {{python}} scripts/eval/run.py --private

# --- running --------------------------------------------------------------

# REEF_DEV_INSECURE lifts the startup guard that otherwise refuses to serve
# HTTP without an auth provider, and REEF_DEV_PRINCIPAL_EMAIL stands in for a
# signed-in person. Both are dead in production, where neither is set.

# Serve the app over HTTP with auth disabled. Local development only.
dev email="wouter@example.test":
    PORT=8000 REEF_DEV_INSECURE=1 REEF_DEV_PRINCIPAL_EMAIL={{email}} \
      {{python}} -m reef.server

# Run the background worker against the local database.
worker:
    {{python}} -m reef.worker

# Print how often each tool was called and how the calls ended, over the last
# whole days (7 to 90): totals only.
call-totals days="30":
    {{python}} scripts/call_totals.py --days {{days}}

# Print how many searches missed at least one read, over the last whole
# days (7 to 89): totals only.
search-totals days="30":
    {{python}} scripts/search_totals.py --days {{days}}

# Run the frontend dev server against a local backend.
dev-frontend:
    cd frontend && bun run dev

# Build the frontend into frontend/dist, which the server serves at /app.
build-frontend:
    cd frontend && bun run build

# Run the MCP server over stdio, as a local assistant would.
stdio email="wouter@example.test":
    REEF_DEV_PRINCIPAL_EMAIL={{email}} {{python}} -m reef.server
