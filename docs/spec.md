# rif — design

Written 5 Aug 2026, revised the same day. The store moved from git repos to
Postgres, and retrieval moved from search to whole-corpus loading. See
"Supersedes" at the end.

## Purpose

Give both members of a household an assistant with long-term memory, over a
shared household layer plus a private layer each, reachable from surfaces with
no filesystem and no GitHub account — chiefly the Claude mobile app.

## Shape

A small headless Python app, deployed, owning a Postgres database and an
object store, exposing a remote MCP server. No web UI. Every surface — her
Claude mobile app, his Claude Code, any later PWA or WhatsApp adapter — is a
client of the same MCP.

One store, one write path, one enforcement point.

## Retrieval: load everything, don't search

At this scale the whole corpus fits in context. Thirteen pages today is roughly
60 KB of markdown; a hundred pages would be under half a megabyte. The primary
tool therefore returns **the entire contents of every space the principal can
see, in one call**, and the model works from complete knowledge rather than
retrieval hits.

This removes an entire category of failure. There is no ranking to tune, no
embedding to stale, no "the model didn't find the page that existed." It also
removes full-text search from v1 — it would be machinery serving no one.

Two guards, because this cannot hold forever:

- `load_context` returns a `truncated` flag and an explicit note in the payload
  when the corpus exceeds a configured token budget. It degrades to index plus
  most-recently-updated pages. **It never silently returns less than
  everything** — a partial context that looks complete is the one failure mode
  worse than slow retrieval.
- The payload carries a `version` (max `updated_at` across the spaces) so a
  client can skip a reload when nothing changed.

When the corpus outgrows context for real, the fallback is index-plus-selective
read, which the existing `read_page` tool already provides. That is an addition,
not a rewrite.

## Data model

| Table | Purpose |
|---|---|
| `persons` | Two rows. Email is identity. |
| `spaces` | `kind` is `personal` or `household`. |
| `memberships` | Person ↔ space. The entire access model. |
| `pages` | `(space_id, path)` unique. Markdown body; title and tags as columns. |
| `revisions` | Append-only. Prior body, author, message, timestamp. |
| `attachments` | Image metadata plus a `description` — see below. |

`revisions` replaces git history and is better for this purpose because it is
queryable: "what did we know about the boiler in March" is a `WHERE` clause
rather than a `git log -S`.

## Images

Postgres is the wrong place for blobs and the right place for everything about
them. Bytes go to S3-compatible object storage — Tigris if hosting on Fly, R2
otherwise; metadata rows stay in Postgres.

Three rules:

1. **Images inherit the space ACL.** No public URLs, ever. `read_image` issues a
   signed URL with a short expiry, and only after the same accessor check as a
   page read. Returning small images inline as MCP image content is a later
   refinement, not v1.
2. **Every attachment carries a text `description`,** generated at upload time
   and stored alongside the metadata. This is what makes images work in a
   load-everything design: the bytes cannot go into context every turn, but the
   descriptions can. The model sees "a photo of the boiler's model plate reading
   Vaillant ecoTEC VU 246/5-5" in its context and calls `read_image` only when
   the pixels actually matter.
3. Attachments belong to a space and optionally to a page.

## Access control

The privacy boundary used to be GitHub's to enforce. It is now this
application's, which is a genuine downgrade in assurance and makes this the most
review-critical code in the repo.

**Every content read and write goes through one accessor** taking an
authenticated principal and a space alias, resolving it against `memberships`
and raising otherwise. No query outside that module touches `pages`,
`revisions`, or `attachments`. A test asserts that a cross-space read fails, and
it is not optional.

Tools speak in **aliases** — `personal` and `household` — never space ids or
names. The alias resolves per principal, so the same call means her personal
space for her and his for him, and neither can name the other's.

## Tool surface

| Tool | Notes |
|---|---|
| `load_context()` | **Primary.** Everything the principal can see, plus `version` and `truncated`. |
| `read_page(space, path)` | Single page. Exists for the post-growth fallback. |
| `read_image(space, path)` | Inline image content or a short-lived signed URL. |
| `remember(fact, space="personal")` | **Private by default in the signature**, not in prose. Appends to that space's inbox page. |
| `write_page(space, path, body, ...)` | Full page write. |
| `edit_section(space, path, old_text, new_text, ...)` | Surgical edit per the protocol. |
| `promote(path, confirm)` | Personal → household. Explicit confirmation, never inferred, never batched. |

There is no demotion tool, because there is no demotion. Once a fact is in the
household space, the other person has read it or may have. `promote`'s
confirmation is the only gate that exists, and its description says so.

## Protocol delivery

The operating protocol lives as a page at `meta/protocol.md` in the household
space; each person's persona lives at `meta/persona.md` in their personal space.
The server concatenates them into the MCP `instructions` returned on initialize.

The protocol stays ordinary content — editable through the same tools, with the
same revision history — and reaches a phone that has no filesystem to load a
manual from.

## Git mirror

A scheduled job renders every page to markdown with frontmatter and commits it
to a mirror repo. Attachments export alongside.

**One-way, app → git.** Hand-edits to the mirror are clobbered. Bidirectional
sync was refused for Notion and is refused here for the same reason: the
conflict-resolution problem never ends.

Nothing reads from the mirror. It exists so the knowledge survives the
application — portable markdown, offline-readable, restorable if the deployment
is lost.

## Surfaces

| Surface | Transport | Who |
|---|---|---|
| Claude mobile app | remote HTTP, OAuth | Her |
| Claude Code | remote HTTP, OAuth | Him |
| Local development | stdio | Him |

Everything except OAuth is testable over stdio. That is deliberate: auth is
isolated to one late phase, so a painful OAuth never blocks a working server.

## Stack

Python 3.13, uv, ruff. FastMCP. SQLAlchemy 2.0 async with Alembic. pytest
against a real Postgres — mocked repositories cannot catch the constraint and
isolation bugs that matter here. Fly.io with Fly Postgres and Tigris.

## Out of scope for v1

Web UI, PWA, WhatsApp adapter, push notifications, full-text search, more than
two people, and any sharing granularity finer than a space.

The PWA and a WhatsApp bot remain viable later and neither is wasted work: both
are clients of this MCP. Keep agent-facing logic free of transport knowledge.

## Gating check

**Before Phase 4**, verify a custom connector is available on her Claude plan
tier and exposed in the mobile app rather than on claude.ai web only. Test by
adding any public MCP server as a custom connector and opening the mobile app.

If connectors are web-only, phases 1–3 are unaffected — store, access model and
tools all survive — and only the surface decision reopens.

## Security notes

Private repo, out of any team a client is added to. No secrets in the repo;
configuration from the environment. The accessor module and its tests are the
review-critical surface. Signed attachment URLs are short-lived and issued only
behind an access check.

## Supersedes

The first draft used three GitHub repos as the store, read and written through
the REST API with per-person scoped tokens, and retrieved by index-plus-grep.

Abandoned because GitHub is a poor database — no queries, no transactions,
~200 ms per read — and because "two people, three spaces" is a membership table,
not three repos plus two tokens plus an identity map.

What the trade cost, and how it is answered here: **portability**, answered by
the git mirror; and **boundary enforcement by construction**, which is now this
application's job and is answered only by discipline — one accessor, no raw
queries, and a test that proves the denial.
