# rif — design

Written 5 Aug 2026, revised twice the same day; rev 3 on 6 Aug. Rev 1: the
store moved from git repos to Postgres, retrieval from search to whole-corpus
loading. Rev 2, after an external architecture review: RLS became the enforced
boundary, promotion became a two-step nonce flow, writes became retry-safe,
the plan reordered risk-first, and mirror automation was cut from v1. Rev 3:
retrieval flipped to index-first with targeted fetches — the MCP mechanizes
the original wiki discipline; bulk loading demoted to a maintenance path. See
"Supersedes" at the end.

**Rev 4, 7 Aug 2026:** coves generalized from household tiers to named
groups with email-bound invites — see
`docs/superpowers/specs/2026-08-07-multi-user-coves-design.md`, which
supersedes this document's "two people, three coves" framing, the
closed-allowlist wording in the access-control section, and the "more than
two people" out-of-scope line.

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
and a **one-line description** — plus file descriptions, and no bodies. The
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

- The index payload carries a `version` derived from per-cove version
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
| `persons` | One row per person. Email is identity; `invited_by_person_id` records who let them in. |
| `coves` | `kind` is `personal` (one per person) or `shared` (any number). Every cove has one accountable `owner_person_id`. |
| `memberships` | Person ↔ cove, with a `role`. The entire access model. |
| `pages` | `(cove_id, path)` unique. Markdown body; title and tags as columns. |
| `revisions` | Append-only. Prior body, author, message, timestamp. |
| `attachments` | File metadata plus a `description` — see below. |

`revisions` replaces git history and is better for this purpose because it is
queryable: "what did we know about the boiler in March" is a `WHERE` clause
rather than a `git log -S`.

## Files

Postgres is the wrong place for blobs and the right place for everything about
them. Bytes go to S3-compatible object storage — Tigris if hosting on Fly, R2
otherwise; metadata rows stay in Postgres.

Three rules:

1. **Files inherit the cove ACL.** No public URLs, ever. `read_file` issues a
   signed URL with a short expiry, and only after the same accessor check as a
   page read. Images use the same storage and can still render inline in pages.
2. **Every attachment carries a text `description`,** generated at upload time
   and stored alongside the metadata. This makes PDFs, documents, archives,
   audio, and images discoverable without loading their bytes every turn. The
   model calls `read_file` when the contents actually matter.
3. Attachments belong to a cove and optionally to a page.

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

Tools address a cove as `personal` or by its slug — never by a cove id, and
never by a personal cove's own slug, which is derived from the person id and
stays inside the server. `personal` resolves per principal, so the same call
means her personal cove for her and his for him, and neither can name the
other's. A slug resolves only through membership, and the denial message is
identical for a cove that does not exist and one the caller is not in, so
probing reveals nothing.

## Sharing model: extract, don't fragment

Sometimes only part of a page should be shared. The rule: **don't share
fragments of a page — extract the section into its own page, and share that
page.** The unit of access stays the cove. The unit of sharing becomes as
small as you like, because a page can be as small as you like.

How it works: `prepare_to_share` takes a `dest_cove` (which shared cove, from
`list_coves`), an optional `section` (the exact text to extract) and a
`dest_path` (the new page's name). The disclosure the user must approve is
exactly the extracted text, together with the destination's current member
list. On confirm, one transaction creates the new page in that cove, replaces the section in the
source page with a marker pointing at it, and consumes the nonce. The rest of
the source page never leaves the personal cove — and neither does its
revision history.

Why not per-section permissions on one page:

1. **It moves the security boundary.** RLS enforces access per page-in-cove.
   Per-section rules would move enforcement into application code that must
   slice documents correctly every time — the correct-if-nobody-slips pattern
   rev 2 removed.
2. **Fragments leak through context.** A paragraph under a private heading
   carries that heading's meaning with it. Extraction forces the shared text
   to stand alone, so the owner sees exactly what the reader will see.
3. **You can't hold it in your head.** "This page is shared with X" is a fact
   a person can remember. "Paragraphs two and four are visible to X" is not —
   and a privacy model you can't hold in your head gets breached by accident.

**Audiences generalize the same way.** A cove is really an audience. Sharing
with a third person (an accountant, a friend) is one new cove, one
membership row, one allowlist row — data, not a redesign. The alias mapping
in `resolve_cove` grows a data-driven entry when the first real third
audience appears; nothing is built speculatively before then.

Deferred refinement: **transclusion** — the owner's page renders extracted
sections back inline by reference, reading only from coves the viewer can
already see. A reading convenience, not an access path.

Known cost: knowledge fragments across more, smaller pages, which makes the
maintenance routine's cross-cove contradiction check more important, not
less.

## Tool surface

| Tool | Notes |
|---|---|
| `load_index()` | **Primary — the first call of every conversation.** Every page's path, title, tags, one-line description, and resolved references, plus described files and a cache `version`. No bodies. |
| `read_pages(cove, paths)` / `read_page` | Targeted fetches, driven by the index. `read_page` takes an optional `as_of`: the page reconstructed from its revisions as it stood at that moment, under the same RLS as a present-day read — the "what did we know about the boiler in March" query the data-model section promises, exposed as a tool. |
| `search_pages(query, cove?, limit?)` | Postgres FTS over titles and bodies, run inside the same armed transaction as every read, so RLS scopes it: a search can only rank pages the caller could read, and a forgotten filter returns nothing. Returns snippets to drive `read_pages`, never a substitute for reading. `websearch_to_tsquery` parses the query, so malformed input cannot error. No embeddings — see the Stack note. |
| `whats_new(since?)` | The awareness surface: page writes (author, message) and file arrivals across accessible coves, newest first, defaulting to the last 7 days. Runs under the same armed RLS session, so another person's personal activity is invisible by construction. Author names resolve through the roster functions, like every surface that shows who is in the room. |
| `load_all_context()` | Bulk path for maintenance only. Reports truncation explicitly (`truncated`, `page_count`/`included_count`) so a cut result is detectable. |
| `add_file` / `read_file` | Any MIME type, original filename and mandatory description in; short-lived signed URL out, behind the same ACL. The old image-named tools remain compatibility aliases. |
| `delete_file(cove, key)` | The destructive file tool. Removes the row and bytes; the ACL check runs before object storage is touched. The old `delete_image` name remains a compatibility alias. |
| `remember(fact, cove="personal")` | **Private by default in the signature.** Row-locked, exact-duplicate-safe under retries. |
| `write_page` / `edit_page_section` | Optimistically versioned (`expected_version`); refuse `meta/` paths. |
| `write_pages(cove, pages, message="")` | Batched `write_page`, up to 20 items, one approval tap. One transaction for the whole batch: any item failing (stale `expected_version`, `meta/` path, malformed item, oversize/empty batch) rolls back every write, including earlier items that looked fine. |
| `update_meta_page(..., confirm)` | The only write path to protocol and persona — the pages that steer the assistant. Refuses any cove but `personal`. |
| `list_coves` | Your coves, each with its member list and whether you own it. |
| `create_cove(slug)` / `invite(cove, email, ..., role)` / `remove_member(cove, email)` | Cove administration. Creator is owner; only the owner changes the member list. `invite` returns the disclosure the user must hear before it is called: permanent access to everything in the cove, past and future. `role` is `member` or `viewer` — a viewer reads everything and writes nothing, enforced by the same per-command write policies that have required `role = 'member'` since day one; the invite finally creates the row those policies were waiting for. `rif_admit_member` validates the role where the row is written. |
| `prepare_to_share(path, dest_cove, section?, dest_path?)` → `confirm_share(nonce)` | Sharing, two steps — a whole page, or one extracted section. A bare `confirm=true` proves nothing — the nonce is bound to the principal, the source revision, and a 10-minute expiry; the destination must not already exist; a consumed nonce reports success idempotently so a retry can never copy the stub. |

There is no demotion tool, because there is no demotion. Once a fact is in the
household cove, the other person has read it or may have. The prepare/confirm
pair is the only gate that exists, and `prepare_to_share` returns the exact
disclosure the user must see before agreeing.

Truncation, when the corpus outgrows the budget, is priority-aware: `meta/`
pages first, `core`-tagged pages second, then smallest-first — an old allergy
note must never lose its context slot to a fresh diary entry. Omitted pages
appear with `body=null`, never silently.

## Protocol delivery

The operating protocol lives as a page at `meta/protocol.md` in each person's
personal cove, beside their persona at `meta/persona.md`. Both are per-person,
so `update_meta_page` refuses any cove but `personal`: a `meta/` page in a
shared cove steers nobody, and it would put instruction-shaped text at the top
of every other member's loaded context.
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

The protocol's empty-cove behavior doubles as onboarding: a first conversation
with an empty personal cove introduces the assistant, asks what to call it,
and interviews gently to seed the persona and first pages.

## Export (the exit hatch)

The web app offers current-content exports for one cove or the whole reef as
import-compatible Markdown or readable JSON. A separate **Dump my data** ZIP
includes all current pages, the raw index, revision history, stored file bytes
and metadata, membership display names, and the caller's sharing audit trail.
The original manual Markdown command remains available for operators.

These are **one-way, app → files** portability paths. Bidirectional sync was
refused for Notion and is refused here for the same reason: the
conflict-resolution problem never ends. The full dump is comprehensive for the
authenticated person's visible data, but it is not a database backup: restoring
RLS policies and the complete multi-person access topology still requires the
Postgres/R2 backup path.

## Account deletion

The Export screen also owns the destructive exit. **Delete my data** is behind
two independent guards: an acknowledgement checkbox and the exact phrase
`DELETE`. The API enforces both again, so the UI cannot be bypassed by an
accidental request.

Deletion removes the person's account, personal cove, and any shared cove where
they are the sole member. A shared cove with other people survives: ownership is
transferred deterministically, the departing membership is removed, and author
references in retained revision history are anonymised. Stored bytes belonging
to deleted coves are removed after the database transaction commits; failures
can leave unreachable object-store orphans but cannot leave live metadata
pointing at missing content.

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

1. Compile inbox pages into real pages, per cove.
2. Staleness pass over pages not updated in ~2 months.
3. **Cross-cove contradiction check** — the same fact can now drift between a
   personal page and its household counterpart, and a contradiction in the
   household cove may mean one person is wrong rather than that the page is
   stale. Flag, never silently resolve.

The tidy-up ritual (compile inboxes, staleness sweep, contradiction check)
now ships in the operating protocol and the agent skill, so any assistant
runs it on request. The Monday automation that runs it unasked remains open.

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

As written for v1: web UI, PWA, WhatsApp adapter, push notifications,
full-text search, more than two people, and any sharing granularity finer
than a cove.

Since shipped anyway: the web UI (reefwith.me/app) and multi-user coves
with owner-managed invitations. Still out: PWA, WhatsApp adapter, push
notifications, and finer-than-cove sharing granularity.

Full-text search has since shipped as `search_pages`: Postgres FTS inside
the same RLS session, per the close/adapt/refuse calls in
[`competitor-research.md`](competitor-research.md). The no-vector-database
position above stands — FTS is the index-plus-selective-read escalation that
section already names, not an embeddings turn.

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
~200 ms per read — and because "two people, three coves" is a membership table,
not three repos plus two tokens plus an identity map.

What the trade cost, and how it is answered here: **portability**, answered by
the git mirror; and **boundary enforcement by construction**, which is now this
application's job and is answered only by discipline — one accessor, no raw
queries, and a test that proves the denial.
