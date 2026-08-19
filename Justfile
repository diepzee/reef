# reef — common tasks.
#
# `just` with no arguments lists everything. Start with `just setup`, which
# is safe to re-run.
#
# The database is the part worth knowing about. reef's privacy boundary is
# row-level security, and RLS is only enforced against an *ordinary* role: a
# superuser, or any role holding BYPASSRLS, ignores every policy. The local
# cluster therefore has three roles with different jobs — `rif` owns the
# databases, `rif_authz` owns the policy helper functions and nothing else,
# and `rif_probe` is a non-owner stand-in that the test suite uses to assert
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
    psql -d postgres -v ON_ERROR_STOP=0 -q <<'SQL' 2>/dev/null || true
    CREATE ROLE rif_authz NOLOGIN BYPASSRLS;
    GRANT rif_authz TO rif;
    CREATE ROLE rif_probe WITH LOGIN PASSWORD 'probe' NOSUPERUSER NOBYPASSRLS
      NOCREATEDB NOCREATEROLE NOREPLICATION;
    SQL
    for db in rif rif_test; do
      psql -d "$db" -q <<'SQL'
    GRANT CREATE ON SCHEMA public TO rif_authz;
    GRANT USAGE ON SCHEMA public TO rif_probe;
    SQL
      psql -d "$db" -q -c "GRANT CONNECT ON DATABASE $db TO rif_probe" || true
    done
    echo "roles present: rif_authz (NOLOGIN BYPASSRLS), rif_probe (ordinary)"

# Apply migrations to the development database.
migrate:
    {{python}} scripts/migrate.py

# Open a psql shell on the development database as the app role.
psql:
    PGPASSWORD=rif psql -h localhost -p 5433 -U rif -d rif

# Drop and rebuild the *test* database's schema. Development data is untouched.
db-reset-test:
    #!/usr/bin/env bash
    set -euo pipefail
    PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d rif_test -q <<'SQL'
    DROP SCHEMA public CASCADE;
    CREATE SCHEMA public;
    GRANT ALL ON SCHEMA public TO rif;
    GRANT USAGE ON SCHEMA public TO rif_probe, rif_authz;
    GRANT CREATE ON SCHEMA public TO rif_authz;
    SQL
    echo "rif_test schema reset; the next test run rebuilds it"

# --- tests and checks -----------------------------------------------------

# Everything CI would run: lint, format check, backend and frontend tests.
test: lint test-py test-js

# Every worktree shares one rif_test, and the schema fixture rebuilds the
# helper functions globally — so two suites at once clobber each other and
# the second reports a screenful of failures that are not about the code.
# scripts/run_tests.py holds a machine-wide lock so the second one waits.

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

# --- running --------------------------------------------------------------

# REEF_DEV_INSECURE lifts the startup guard that otherwise refuses to serve
# HTTP without an auth provider, and REEF_DEV_PRINCIPAL_EMAIL stands in for a
# signed-in person. Both are dead in production, where neither is set.

# Serve the app over HTTP with auth disabled. Local development only.
dev email="wouter@example.test":
    PORT=8000 REEF_DEV_INSECURE=1 REEF_DEV_PRINCIPAL_EMAIL={{email}} \
      {{python}} -m reef.server

# Run the frontend dev server against a local backend.
dev-frontend:
    cd frontend && bun run dev

# Build the frontend into frontend/dist, which the server serves at /app.
build-frontend:
    cd frontend && bun run build

# Run the MCP server over stdio, as a local assistant would.
stdio email="wouter@example.test":
    REEF_DEV_PRINCIPAL_EMAIL={{email}} {{python}} -m reef.server
