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

The v1 build is complete and reviewed — schema, access control, index and
page reads, versioned writes, section-level sharing, protocol delivery,
images, import, backup, and export. 62 tests pass against a real Postgres.

What remains needs accounts, dashboards, and two phones: the OAuth gating
check against WorkOS AuthKit, the Railway deploy, the production restore
drill, the real import, and measuring the context ceiling from an actual
phone. [`docs/runbook.md`](docs/runbook.md) explains each step, why it
exists, and what done looks like.

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

The last migration in `src/rif/piccolo_migrations/` seeds the real household
and still carries `<HER-EMAIL>` and `<HER-NAME>` placeholders. Fill both in
before running it anywhere real.
