#!/usr/bin/env bash
# Create one local spike database, ready for the app, and migrate it.
#
# Usage: spike/fast_index/makedb.sh reef_spike_<name>
#
# Refuses any name that does not start with reef_spike_, so it cannot touch
# the development, test or other worktrees' databases.
set -euo pipefail
db="$1"
[[ "$db" == reef_spike_* ]] || { echo "refused: $db is not a reef_spike_* database" >&2; exit 1; }
here="$(cd "$(dirname "$0")/../.." && pwd)"
psql() { PGPASSWORD=postgres command psql -h localhost -p 5433 -U postgres -v ON_ERROR_STOP=1 -q "$@"; }
psql -d postgres -c "CREATE DATABASE $db OWNER reef"
psql -d "$db" <<SQL
GRANT CREATE ON SCHEMA public TO reef_authz;
GRANT USAGE ON SCHEMA public TO reef_probe;
GRANT CONNECT ON DATABASE $db TO reef_probe;
CREATE EXTENSION IF NOT EXISTS vector;
SQL
REEF_DATABASE_URL="postgresql://reef:reef@localhost:5433/$db" "$here/.venv/bin/python" "$here/scripts/migrate.py"
echo "ready: $db"
