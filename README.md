# rif

A remote MCP server that gives a household long-term assistant memory.

Two people, three spaces: a private space each, plus one they share. Both
reach it from surfaces that have no filesystem and no GitHub account —
chiefly the Claude mobile app — by adding this server as a custom connector.

The store is Postgres. Row-Level Security is the privacy boundary, so a
forgotten filter in application code fails closed rather than leaking. Tools
speak in aliases (`personal`, `household`) that resolve per principal, so
neither person can name the other's space.

Retrieval is index first, then fetch — the wiki pattern, mechanized. The
assistant calls `load_index` for a map of every page it may see (path, title,
tags, one-line description, no bodies), then fetches what the conversation
needs with `read_pages`, and keeps fetching as topics come up. The index is
computed from the store on every call, so it cannot drift.

A manual export renders every page back to markdown with frontmatter, so the
knowledge outlives the deployment. One-way, app to files.

Design: [`docs/spec.md`](docs/spec.md).
Going live: [`docs/runbook.md`](docs/runbook.md).
Backup and restore: [`docs/restore.md`](docs/restore.md).

## Status

**Live since 6 Aug 2026**, deployed on Railway behind WorkOS AuthKit and in
daily use from Claude Code. The v1 build is complete and reviewed — schema,
access control, index and page reads, versioned writes, section-level
sharing, protocol delivery, images, import, backup, and export. 64 tests
pass against a real Postgres. The connector gate passed, the real service is
deployed, and the personal corpus is imported.

What remains, in the order it matters — [`docs/runbook.md`](docs/runbook.md)
explains each step, why it exists, and what done looks like:

- **There is no independent backup.** R2 is not set up and the backup cron
  does not exist, so `scripts/backup.py` — which streams `pg_dump` straight
  to R2 — has never run. Railway's managed Postgres snapshots are the only
  copy of a corpus that exists nowhere else. When R2 does exist, the backup
  connection needs its own `BYPASSRLS` role, and the restore drill still has
  to run for real: so far it has only been rehearsed against local Postgres.
- **Images do not work yet.** `add_image` and `read_image` build their S3
  client per call, so with R2 unconfigured both raise a bare
  `ValueError: Invalid endpoint:`. Everything else is unaffected.
- **Only one person is seeded**, so rif is single-user in practice. Adding
  the second member is a migration plus her exact email.
- `meta/persona.md` is still the placeholder written during the first
  end-to-end test.
- The context ceiling has not been measured from a phone.

## Development

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d              # Postgres on 5433, creating rif and rif_test
uv run pytest                     # builds its own schema in rif_test
uv run python scripts/migrate.py  # only needed for the rif dev database
```

The local Postgres deliberately does not run the app as a superuser — see the
comment in `docker-compose.yml`. Superusers carry `BYPASSRLS`, which would
make the security tests pass without proving anything.

The last migration in `src/rif/piccolo_migrations/` seeds one person, his
personal space, and the household space. The second member is deliberately
absent rather than a placeholder: her email is the key her first login binds
against, and migrations do not re-run to correct a guess. Adding her is a
new migration — see Phase 2 of [`docs/runbook.md`](docs/runbook.md).
