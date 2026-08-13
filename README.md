# rif

A remote MCP server that gives a group long-term assistant memory.

Memory lives in **spaces**: one private space per person, created at first
sign-in, plus any number of **named shared spaces** — a household, a school
circle, an accountant, a small project. A space is a group of people, started
by whoever needs it, joined by email invitation from its owner. Everyone
reaches it from the assistant they already use — Claude, the ChatGPT desktop
app, or Codex — by adding this server as a remote MCP connector.

The store is Postgres. Row-Level Security is the privacy boundary, so a
forgotten filter in application code fails closed rather than leaking. A space
is addressed by `personal` or by its slug, resolved per principal, so nobody
can name anybody else's private space.

That boundary only holds if the app connects as an ordinary role: a superuser
carries `BYPASSRLS` and ignores every policy. The app therefore runs as
`rif_app` (DML, no DDL) while migrations and backups use a separate admin
credential — see `scripts/provision_app_role.py`.

Retrieval is index first, then fetch — [Andrej Karpathy's LLM Wiki
pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
adapted for a shared, permissioned MCP service. The assistant calls
`load_index` for a map of every page it may see (path, title, tags, one-line
description, no bodies), then fetches what the conversation needs with
`read_pages`, and keeps fetching as topics come up. The index is computed from
the store on every call, so it cannot drift.

Page bodies are Markdown from the start. The web app exports current content as
Markdown or JSON and offers a full data dump with history and stored file bytes,
so the knowledge outlives the deployment. One-way, app to files.

Design: [`docs/spec.md`](docs/spec.md).
Going live: [`docs/runbook.md`](docs/runbook.md).
Backup and restore: [`docs/restore.md`](docs/restore.md).
Market landscape: [`docs/competitor-research.md`](docs/competitor-research.md).

## CLI and agent skill

The `uv`/PyPI package (`uv tool install reef-cli`) is the full CLI: it mirrors
the complete MCP tool surface as named commands using shell-friendly hyphens
(`load_index` becomes `load-index`), plus `reef call` for an exact MCP tool
name and a JSON object as lossless passthrough. Every result is JSON; an MCP
application error such as `not_found` also exits nonzero.

The npm package (`npm install -g @haai/reef-cli`) is a minimal client: `reef login`,
`reef tools` to list the live MCP schemas, and `reef call <tool> '<json>'` —
the same passthrough covers everything the full CLI's named commands do, just
without the per-tool shortcuts. Both distributions install a `reef` command
and both sign in through the same secure browser flow, but each caches its
own OAuth tokens, so logging in with one doesn't log in the other.

```bash
uv tool install reef-cli
reef login
reef load-index
reef get-operating-protocol
reef read-pages personal profile.md preferences.md
```

OAuth tokens are cached in a user-private config file. Set `REEF_MCP_URL` to
use another endpoint (the default is `https://reefwith.me/mcp`), or provide a
session-scoped `REEF_ACCESS_TOKEN` for a headless invocation. Run `reef tools`
for the live MCP schemas and `reef <command> --help` for local arguments.

Large Markdown and JSON inputs can come from files or stdin, and file uploads
are encoded automatically:

```bash
reef write-page personal plans.md --body-file ./plans.md \
  --message "Add the summer plan" --title Plans
reef write-pages personal @./pages.json --message "Reorganize notes"
reef add-file personal ./lease.pdf --description "Signed rental agreement"
reef call read_pages '{"space":"personal","paths":["plans.md"]}'
```

The matching agent skill is [`skills/reef/SKILL.md`](skills/reef/SKILL.md). It
adds the retrieval protocol, private-by-default writes, optimistic locking,
and explicit confirmation rules for sharing, invitations, persona changes,
member removal, and file deletion.

## Status

**Live since 6 Aug 2026**, deployed on Railway behind WorkOS AuthKit and in
daily use from Claude Code. The v1 build is complete and reviewed — schema,
access control, index and page reads, versioned writes, section-level
sharing, protocol delivery, general file storage, import, backup, and export. Tests
pass against a real Postgres. The connector gate passed, the real service is
deployed, and the personal corpus is imported.

**Browser frontend** ships in the same image. Members can browse and edit pages
at `/app`, and owners can manage spaces from there — a React app built with Bun,
served by the same service. The MCP surface is unchanged; the web UI is additive.

**Multi-user spaces** landed next: spaces are named groups rather than a fixed
household tier, each with an accountable owner who invites people by email
(`create_space`, `invite`, `remove_member`), sharing targets any space the
person belongs to and discloses that space's member list, and a new person's
personal space plus starter pages appear at their first sign-in. Read-only
memberships are enforced by the RLS policies from day one, though nothing yet
creates one.

**Everything still outstanding is tracked in one place: the "Open items"
list at the top of [`docs/runbook.md`](docs/runbook.md)**, grouped by what
each item is waiting on. The headlines:

- **Backups run, but only by hand.** One real dump exists in R2 and the
  restore drill passed against it — counts matched production and RLS
  survived. The daily cron service is not created yet, so nothing is
  automatic. See Phase 4 of [`docs/runbook.md`](docs/runbook.md).
- **Only one person is seeded**, so rif is single-user in practice until the
  first invite goes out. That is now a tool call by the space's owner, not a
  migration.
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

The migrations in `src/rif/piccolo_migrations/` seed one person, his personal
space, and the `school` shared space he owns. Nobody else is seeded, by design:
an email is the key a first login binds against, and migrations do not re-run
to correct a guess. Everyone after this first person arrives through `invite` —
see Phase 2 of [`docs/runbook.md`](docs/runbook.md).
