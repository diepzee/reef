#!/usr/bin/env bash
# Run the worker on each named spike database until its queue is empty, then stop it.
#
# Usage: spike/fast_index/embed_all.sh <logdir> reef_spike_a reef_spike_b ...
#
# One database at a time, so the local embedding service is never shared and
# no worker outlives its queue.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
logs="$1"; shift
export REEF_EMBEDDING_URL=http://127.0.0.1:11434 REEF_EMBEDDING_DAILY_CAP=1000000 REEF_WORKER_POLL_SECONDS=1
pending() { PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d "$1" -Atc \
  "select count(*) from jobs where status in ('queued','running') and run_after <= localtimestamp + interval '10 minutes'"; }
for db in "$@"; do
  [[ "$db" == reef_spike_* ]] || { echo "refused: $db" >&2; exit 1; }
  start=$(date +%s)
  REEF_DATABASE_URL="postgresql://reef:reef@localhost:5433/$db" "$here/.venv/bin/python" -m reef.worker > "$logs/worker_$db.log" 2>&1 &
  pid=$!
  sleep 5
  while [[ "$(pending "$db")" != "0" ]]; do sleep 10; done
  kill "$pid"; wait "$pid" 2>/dev/null || true
  echo "$db embedded in $(( $(date +%s) - start )) s"
done
