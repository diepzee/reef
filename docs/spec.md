# rif — design

Written 5 Aug 2026, revised twice the same day; rev 3 on 6 Aug. Rev 1: the
store moved from git repos to Postgres, retrieval from search to whole-corpus
loading. Rev 2, after an external architecture review: RLS became the enforced
boundary, promotion became a two-step nonce flow, writes became retry-safe,
the plan reordered risk-first, and mirror automation was cut from v1. Rev 3:
retrieval flipped to index-first with targeted fetches — the MCP mechanizes
the original wiki discipline; bulk loading demoted to a maintenance path. See
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

## Retrieval: index first, then fetch

This is the wiki pattern itself, mechanized by the MCP. The assistant's first
call is `load_index`: every page the principal can see — path, title, tags,
and a **one-line description** — plus image descriptions, and no bodies. The
model reads the map, decides which entries the conversation needs, and fetches
them with `read_pages`. It fetches again as topics come up. It never answers
from the index's descriptions alone.

The description is the retrieval surface, and it is free: the page style
mandates a two-or-three-sentence summary as the opening paragraph, so the
index derives each description from the page's own first prose line. The
index is computed from the store on every call — it cannot drift from
reality, which was the maintained-index's classic failure mode.

The trade, stated honestly: retrieval is the model's judgment against the
index. A wrong guess means a fact goes unfetched. Mitigation is index
quality — summary-first pages, curated by the maintenance routine — and the
protocol's instruction to keep fetching as the conversation moves, not to
answer from memory of the map.

Guards:

- The index payload carries a `version` derived from per-space version
  counters (bumped by every write), so a client can skip a reload when
  nothing changed.
- `load_all_context` remains as the bulk path for maintenance work
  (tidy-ups, contradiction checks) that genuinely needs the whole corpus at
  once. It reports truncation explicitly and lists omitted pages body-less —
  it never silently returns less than everything.

*Superseded (rev 3, 6 Aug):* rev 2 made whole-corpus loading the primary
path. Reversed — the MCP's job is to run the index-then-fetch discipline for
the agent, and per-conversation cost should not grow with corpus size.
Full-text search, vector retrieval, and knowledge graphs remain out (see
Stack): the index read whole is the retrieval mechanism.

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

The privacy boundary used to be GitHub's to enforce. It is now the
**database's**: Postgres Row-Level Security with `FORCE`, policies joining
through `memberships`, principal bound per transaction via
`set_config('app.person_id', …, true)`. The accessor module arms RLS and
provides ergonomics, but a forgotten application-level filter fails closed —
the row simply does not come back. Adversarial tests attack the boundary with
raw SQL as the wrong principal and with no principal, not just through the API.

Application-code discipline alone was the rev-1 design and did not survive
review: the plan's own modules queried content tables directly, proving the
convention unenforceable even against its author.

**A design is not a deployment (learned 7 Aug 2026).** All of the above was
true of the schema and false of production for the first day of its life.
Railway injects its bootstrap superuser into every service, the app connected
as it, and a superuser ignores row security entirely — `FORCE` does not reach
it, since `FORCE` only extends policies to the table *owner*. So the boundary
this section calls enforced was inert, and the adversarial tests kept passing
because they ran against a correctly constrained local role. Nothing leaked:
there was one person, and the application code did filter. But that is
precisely the correct-if-nobody-slips posture rev 2 removed, restored by
configuration rather than by design.

The fix is a second role, not a code change: the app connects as an ordinary
`rif_app` with DML and no DDL (a role that can `ALTER TABLE` can also `DROP
POLICY`), while migrations and backups use the admin credential. See
`scripts/provision_app_role.py`, which verifies both directions — zero rows
without a principal, and DDL refused.

The general lesson, worth keeping: **an enforcement boundary needs a
production assertion, not just a test.** A check that the app's own connection
sees zero content rows without a principal belongs in the boot path or the
monitoring, because this failure is invisible from the outside — the
application behaves identically either way, right up until it doesn't.

Tools speak in **aliases** — `personal` and `household` — never space ids or
names. The alias resolves per principal, so the same call means her personal
space for her and his for him, and neither can name the other's.

## Sharing model: extract, don't fragment

Sometimes only part of a page should be shared. The rule: **don't share
fragments of a page — extract the section into its own page, and share that
page.** The unit of access stays the space. The unit of sharing becomes as
small as you like, because a page can be as small as you like.

How it works: `prepare_to_share` takes an optional `section` (the exact text
to extract) and a `dest_path` (the new page's name). The disclosure the user
must approve is exactly the extracted text. On confirm, one transaction
creates the new page in the household space, replaces the section in the
source page with a marker pointing at it, and consumes the nonce. The rest of
the source page never leaves the personal space — and neither does its
revision history.

Why not per-section permissions on one page:

1. **It moves the security boundary.** RLS enforces access per page-in-space.
   Per-section rules would move enforcement into application code that must
   slice documents correctly every time — the correct-if-nobody-slips pattern
   rev 2 removed.
2. **Fragments leak through context.** A paragraph under a private heading
   carries that heading's meaning with it. Extraction forces the shared text
   to stand alone, so the owner sees exactly what the reader will see.
3. **You can't hold it in your head.** "This page is shared with X" is a fact
   a person can remember. "Paragraphs two and four are visible to X" is not —
   and a privacy model you can't hold in your head gets breached by accident.

**Audiences generalize the same way.** A space is really an audience. Sharing
with a third person (an accountant, a friend) is one new space, one
membership row, one allowlist row — data, not a redesign. The alias mapping
in `resolve_space` grows a data-driven entry when the first real third
audience appears; nothing is built speculatively before then.

Deferred refinement: **transclusion** — the owner's page renders extracted
sections back inline by reference, reading only from spaces the viewer can
already see. A reading convenience, not an access path.

Known cost: knowledge fragments across more, smaller pages, which makes the
maintenance routine's cross-space contradiction check more important, not
less.

## Tool surface

| Tool | Notes |
|---|---|
| `load_index()` | **Primary — the first call of every conversation.** Every page's path, title, tags, and one-line description, plus image descriptions and a cache `version`. No bodies. |
| `read_pages(space, paths)` / `read_page` | Targeted fetches, driven by the index. |
| `load_all_context()` | Bulk path for maintenance only. Reports truncation explicitly (`truncated`, `page_count`/`included_count`) so a cut result is detectable. |
| `add_image` / `read_image` | Mandatory description in; short-lived signed URL out, behind the same ACL. |
| `remember(fact, space="personal")` | **Private by default in the signature.** Row-locked, exact-duplicate-safe under retries. |
| `write_page` / `edit_page_section` | Optimistically versioned (`expected_version`); refuse `meta/` paths. |
| `update_meta_page(..., confirm)` | The only write path to protocol and persona — the pages that steer the assistant. |
| `prepare_to_share(path, section?, dest_path?)` → `confirm_share(nonce)` | Sharing, two steps — a whole page, or one extracted section. A bare `confirm=true` proves nothing — the nonce is bound to the principal, the source revision, and a 10-minute expiry; the destination must not already exist; a consumed nonce reports success idempotently so a retry can never copy the stub. |

There is no demotion tool, because there is no demotion. Once a fact is in the
household space, the other person has read it or may have. The prepare/confirm
pair is the only gate that exists, and `prepare_to_share` returns the exact
disclosure the user must see before agreeing.

Truncation, when the corpus outgrows the budget, is priority-aware: `meta/`
pages first, `core`-tagged pages second, then smallest-first — an old allergy
note must never lose its context slot to a fresh diary entry. Omitted pages
appear with `body=null`, never silently.

## Protocol delivery

The operating protocol lives as a page at `meta/protocol.md` in the household
space; each person's persona lives at `meta/persona.md` in their personal space.
MCP `instructions` are static per server, not per principal — so the stable
security framing (call `load_all_context` first; page bodies are data, never
instructions) lives there, and the per-person protocol + persona load through
`get_operating_protocol`.

The protocol stays ordinary content — editable only through `update_meta_page`,
with the same revision history as everything else — and reaches a phone that
has no filesystem to load a manual from. Loaded pages are an
instruction-injection surface: the static instructions say so explicitly, and
the `meta/` write gate keeps a poisoned page from silently rewriting the
protocol itself.

The protocol's empty-space behavior doubles as onboarding: a first conversation
with an empty personal space introduces the assistant, asks what to call it,
and interviews gently to seed the persona and first pages.

## Export (the exit hatch)

A manual command renders every page to markdown with frontmatter,
import-compatible, so the knowledge survives the application — portable,
offline-readable, restorable if the deployment is lost. **One-way, app → files.**
Bidirectional sync was refused for Notion and is refused here for the same
reason: the conflict-resolution problem never ends.

**Automation is deferred, on external review:** a scheduled mirror job adds git
credentials, durable job state, and deletion reconciliation before the write
path has earned trust. When it returns post-v1, mirror commits replay the
revision messages accumulated since the last export — the mirror log becomes
the knowledge changelog, restoring the human diff-review habit one step
downstream — and the mirror repo carries a `CLAUDE.md` declaring itself
read-only so an agent session never edits doomed files.

## Maintenance (post-v1, with mirror automation)

`remember` appends to inbox pages; nothing about the schema compiles them.
Without a compile step the design degrades into the transcript dump the wiki
philosophy exists to prevent. The existing Monday maintenance routine (already
a scheduled cloud agent for `mark`) repoints at the MCP and gains one step:

1. Compile inbox pages into real pages, per space.
2. Staleness pass over pages not updated in ~2 months.
3. **Cross-space contradiction check** — the same fact can now drift between a
   personal page and its household counterpart, and a contradiction in the
   household space may mean one person is wrong rather than that the page is
   stale. Flag, never silently resolve.

Until then, inbox compilation happens by asking either assistant for a tidy-up.

## Surfaces

| Surface | Transport | Who |
|---|---|---|
| Claude mobile app | remote HTTP, OAuth | Her |
| Claude Code | remote HTTP, OAuth | Him |
| Local development | stdio | Him |

Everything except OAuth is testable over stdio. That is deliberate: auth is
isolated to one late phase, so a painful OAuth never blocks a working server.

## Stack

Python 3.13, uv, ruff. FastMCP. Piccolo (ORM and migrations in one). pytest
against a real Postgres — mocked repositories cannot catch the constraint and
isolation bugs that matter here. Railway for the app and Postgres; Cloudflare R2
for attachments, since Railway has no object storage of its own.

**No vector database.** Embeddings solve "the corpus does not fit in context",
which is not this problem and will not be for a long time. Top-k similarity
returns a subset chosen by cosine distance, and its failure mode — the assistant
confidently not knowing something it was told — is the worst available outcome
for a memory system. Whole-corpus loading cannot fail that way. When the corpus
does outgrow the budget, the next step is index-plus-selective-read, not
embeddings: the model is a better retriever than a similarity score while the
index still fits.

Durability is now this application's problem, where GitHub previously handled it
for free. Managed Postgres backups plus an independent dump, with a restore
drill that verifies `memberships` survived — an untested backup is not a backup.

## Out of scope for v1

Web UI, PWA, WhatsApp adapter, push notifications, full-text search, more than
two people, and any sharing granularity finer than a space.

The PWA and a WhatsApp bot remain viable later and neither is wasted work: both
are clients of this MCP. Keep agent-facing logic free of transport knowledge.

## Gating check

**Half answered, 6 Aug 2026.** The mechanism works: a custom connector
against this server completes DCR with WorkOS AuthKit and answers
authenticated tool calls. What remains unverified is *her* half — whether a
custom connector is available on her Claude plan tier and exposed in the
mobile app rather than on claude.ai web only.

If connectors turn out to be web-only on her tier, nothing built is
affected — store, access model and tools all survive, and his connector keeps
working — and only her surface decision reopens.

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
