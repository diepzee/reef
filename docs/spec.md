# rif — design

Written 5 Aug 2026. Spec only; nothing implemented.

## Purpose

Give a non-technical household member an assistant with long-term memory, backed
by the same markdown-in-git store the owner uses, without requiring a GitHub
account, a local checkout, or any developer tooling.

## Dependency and contract

`rif` depends on the layer architecture in `mark/meta/architecture.md`. The
dependency runs one way only — that repo does not know this one exists, and the
knowledge base works fully without it.

The contract is three things:

1. **Repo layout** — `wiki/`, `sources/`, `_index/index.md`, `AGENTS.md`.
2. **The `AGENTS.md` protocol** — compile don't dump, surgical edits, supersede
   don't delete, index in the same commit, flag conflicts out loud.
3. **Credential scoping** — a credential reaches exactly the layers one person
   may see, and no more.

## Transport

**Remote, streamable HTTP.** Not stdio. The target surface is the Claude mobile
app, which has no local process to run a server in. This is the single decision
that drives hosting and OAuth into scope; everything else would have been
simpler over stdio.

## Authentication

Claude's custom-connector flow authenticates against the server, so `rif`
implements OAuth. Identity is an email address checked against a two-entry
allowlist — this is an allowlist, not an identity system, and should not grow
into one.

Each identity maps to a GitHub credential scoped to exactly that person's
layers:

| Identity | Token scope |
|---|---|
| Wouter | `mark`, `school` |
| Wife | *(hers)*, `school` |

**Never a single credential covering all three repos**, even though the code
would "obviously" select correctly. The mapping is the most security-relevant
code in the server and should be a handful of boring, well-reviewed lines. A
session bug then leaks a person's own pages to themselves; it cannot reach a
layer its token has no access to. The boundary does the work instead of the
logic.

## Store access

GitHub REST API — read file, write file with SHA. No clone, no database of
content. Consequences:

- Nothing to sync, works from any device.
- No git on anyone's machine, so no merge conflict ever surfaces to a user.
- Concurrent writes to `school` resolve as last-write-wins with a SHA check: if
  it moved underneath, re-read and retry. Two writers will hit this roughly
  never, and the honest fix when they do is a re-read.

Caching the index in memory or KV is a reasonable optimisation; content is not
cached.

## Tool surface

Tools speak in **spaces**, never repo names. `personal` and `household` resolve
per identity from config, so the same binary serves both people and neither
learns the word `school`.

| Tool | Notes |
|---|---|
| `list_index(space)` | The map. Cheap, called first. |
| `read_page(space, path)` | One page. |
| `search(query, space=None)` | Naive: index plus body grep across the space. |
| `remember(fact, space="personal")` | **Private by default in the signature**, not in prose. |
| `write_page` / `edit_section` | Surgical edits per the protocol. |
| `promote(page, confirm=...)` | Private → household. Requires explicit confirmation; never inferred, never batched. |

There is no demotion tool, because there is no demotion. Once a fact is in
`school`'s history it is readable forever. The `promote` confirmation is the
only gate that exists, and the tool description should say so plainly.

## Protocol delivery

The server reads `AGENTS.md` from the household repo and returns it as the MCP
`instructions` on initialize. A phone has no filesystem to auto-load a manual
from, so this is how the operating protocol reaches the surface — and it means
changing the protocol stays a commit rather than a deploy.

Persona lives in a Claude Project alongside the connector; that is the mobile
equivalent of the owner's `CLAUDE.md`.

## Stack

Python with FastMCP, per standing preference; streamable HTTP; hosted on Fly.io
or equivalent for a few euros a month.

The counterargument, recorded because it may become relevant: OAuth is the
genuinely annoying part of a remote MCP server, and Cloudflare Workers has a
maintained library for exactly this on a platform already in use. If the auth
turns into a weekend of its own, that is the moment to switch rather than push
through.

## Out of scope for v1

PWA, WhatsApp adapter, web push, attachments, block editing, real-time collab,
and search beyond fetch-and-grep.

Both the PWA and a WhatsApp bot were considered and deferred. Neither is wasted
work later: both are adapters over this tool layer. Keep the agent logic in a
module taking `(person, message) -> reply` that never learns what transport it
is behind.

## Gating check

**Before implementation begins**, verify that a custom connector is available on
the wife's Claude plan tier and is exposed in the mobile app rather than on
claude.ai web only. The entire transport decision rests on it. Test by adding
any public MCP server as a custom connector and opening the mobile app.

If connectors turn out to be web-only, the design flips back to a PWA and this
spec needs rewriting above the tool surface — the tools themselves survive.

## Security notes

This repo's code is what enforces the boundary between one person's sessions and
another's private layer. It stays private and out of any team a client is added
to. No secrets in the repo, per the `mark` house rule; tokens come from the
environment.
