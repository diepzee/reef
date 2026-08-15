# rif — competitor research

Written 9 Aug 2026, from a three-track web sweep: the AI-memory-layer
products, consumer and family shared-memory products (including first-party
ChatGPT/Claude features), and the MCP memory-server ecosystem. Pricing and
feature claims were pulled from vendor pages on that date and will drift.

## The question

Does the closest product to rif already exist? rif's specific combination:

1. **Hosted remote MCP** usable from the consumer Claude mobile app as a
   custom connector — no filesystem, no GitHub account.
2. **Named shared spaces joined by email invitation**, plus a **private
   space per person**, in one system.
3. **Per-person privacy enforced at the database** (Postgres RLS), with a
   consent flow for promotion (`prepare_to_share` / `confirm_share`).
4. **Human-editable wiki pages** — index-first retrieval for the assistant,
   a web UI for the people.
5. **An operating protocol as a first-class artifact** — the assistant is
   told when and how to read and write memory.

## The answer

**No product combines all five as of August 2026.** Three come close, each
from a different direction, and none occupies the consumer/household
positioning with a hosted product. The first parties explicitly do not do
it: Claude's memory is per-user even on Team/Enterprise, Cowork Projects
cannot be shared, ChatGPT's shared Projects are Business/Enterprise-only
with scoped project memory, and neither vendor has a family plan. Demand is
visible and unmet — an open feature request for shared team memory in
Claude Code (anthropics/claude-code#38536) and a mid-2026 run of how-to
articles gluing Notion + Composio + Supermemory together to approximate a
shared memory layer.

## The three closest

### Basic Memory Cloud + Teams — closest established product

<https://basicmemory.com> · OSS (AGPL) + hosted cloud · $15–30/seat/mo

The only established product with: a hosted remote connector listed in the
Claude Directory (works on Claude web, desktop, **mobile**, Cowork, and
Claude Code), memory as plain human-editable Markdown with wiki links and a
web editor, and Teams workspaces with email invitations and roles
(admin/editor/viewer).

What it lacks vs rif: per-seat B2B/prosumer pricing that does not map to a
family of four sharing an "accountant" space; no first-class model of one
person belonging to several named spaces *plus* a private space; privacy is
workspace-role-based with no per-person guarantee analogous to RLS and no
consent flow for promoting private material into a shared space.

### Memlord — closest in shape, including the family framing

<https://memlord.com> · AGPL + commercial license · cloud pricing unpublished

Cloud MCP server (Postgres + pgvector) with a claude.ai OAuth connector,
shared workspaces with invite links explicitly marketed for "school,
vacations, household shared with the whole family," per-user isolation, and
a web UI to browse, search, edit, and delete memories, plus JSON export.

What it lacks vs rif: memory is item-based snippets, not a wiki with
index-first retrieval and versioned pages; a small OSS project with no
published consumer pricing and no visible traction.

### Mwe-MCP — closest in philosophy

<https://github.com/Fr4nZ82/mwe-mcp> · AGPL + commercial · self-hosted only

"One governed brain for a household or a team." Multi-user, multi-agent
memory as a human-readable Markdown wiki; **per-fragment ACLs inside a
page**, redacted per reader before text reaches any agent — finer-grained
than rif's per-space RLS; attribution, validity windows, as-of-date
queries; a PWA dashboard with per-reader rendering; overnight
self-reorganization. Running live with four people and three agents since
spring 2026.

What it lacks vs rif: a single self-hosted Rust binary with no hosted
service, no email-invitation onboarding (admin wizard creates users), no
consumer path to the Claude mobile app, and essentially zero distribution
(~5 GitHub stars as of the Show HN, ~July 2026).

## The rest of the field, briefly

- **Context Cloud** (contextcloud.pro, May 2026) — hosted MCP memory with
  shared workspaces, email invitations, RBAC, per-chunk attribution, and a
  web dashboard, free tier with unlimited teammates. Chunk-based rather
  than pages, and positioned squarely at engineering teams. Its marketing
  claims to be the only shared-workspace MCP memory server; Basic Memory
  Teams contradicts that.
- **Memory-MCP by Cedra** (Cloudflare Workers + D1) — remote team memory
  for Claude Code with **index-first retrieval**, proving that pattern is
  no longer unique to rif. Static per-teammate bearer tokens, no private
  spaces, no web UI, negligible traction.
- **The big memory layers** — Mem0/OpenMemory, Supermemory, Zep/Graphiti,
  Letta, Cognee, MemOS, Hindsight, MemoryLake — are all either single-user
  consumer memory or per-end-user developer infrastructure. Mem0 and
  Supermemory own the "personal memory connector" slot in Anthropic's
  directory (841 connectors listed as of July 2026; every memory entry is
  individual memory or company knowledge-base). **Honcho** (Plastic Labs)
  is the strongest architectural rival — peers with scoped
  "what-does-Alice-know-about-Bob" context — but ships as developer
  infrastructure with no consumer surface.
- **Family-AI apps** — familymind, Ohai.ai, Ollie, Milo, and Amazon's
  Alexa+ (the closest first-party household AI: whole-household access,
  app invites, per-member profiles). All are closed assistants with their
  own opaque memory; none plugs into the user's own Claude or exposes
  memory as editable pages. They are "an assistant with a memory," not
  "memory for your assistant."
- **DIY substitutes** — Notion's official remote MCP is the strongest:
  genuinely multi-user, human-editable, permissioned, hosted OAuth. But
  there is no operating protocol, no versioned agent-write discipline, no
  private-plus-shared space model designed for assistant memory; retrieval
  is ad-hoc search over a workspace shaped for humans. **Qontext**
  productizes this for B2B company context. Obsidian+Sync+MCP,
  AnythingLLM, and Khoj (cloud sunset Apr 2026, now self-host only) are
  further variations, none with per-person privacy designed in.

## What this means for rif

- **Durable differentiators:** private-by-default with RLS-enforced
  per-person privacy *inside* a group product; the invitation and consent
  flows; the operating protocol as a first-class artifact; one person in
  several named spaces plus a private space as the core model. No surveyed
  competitor combines any two of these.
- **No longer differentiating:** index-first retrieval (Cedra replicates
  it), hosted remote MCP with OAuth (table stakes — Basic Memory,
  Supermemory, Mem0, Memlord all have it), human-editable Markdown (Basic
  Memory does it well).
- **The gap is real but likely not durable.** Memlord already gestures at
  the household framing; Basic Memory has every ingredient except the
  space model and could pivot; Anthropic could ship shared memory
  first-party — the open feature request shows they know the demand
  exists. The consumer/household position is open today and rif is,
  as far as this sweep found, alone in it.

---

# Addendum: the glama.ai sweep — 15 Aug 2026

glama.ai is the largest MCP directory (~72,000 servers). rif is listed
there as `diepzee/rif`, categorised under Knowledge & Memory, RAG
Systems, and Note Taking. This sweep asked the reverse of the question
above: not "what does rif have that nobody else does" but **"what does
the field have that rif does not"** — and then, per capability, whether
rif should close the gap, adapt the idea, or refuse it on purpose.

## Who is on glama that resembles rif

**Multi-user shared memory (rif's own category).** Basic Memory
Cloud/Teams (covered above; on glama with 40+ releases/yr and same-day
maintainer response), Memlord (`MyrikLD/memlord`, AGPL, self-hosted),
**Lore** (`agentkitai/lore`), SharedMemory.ai, Pathrule
(`pathrule/mcp`), bikky (`bikky-dev/bikky`), Sylex Memory
(E2E-encrypted vaults + shared commons + agent-to-agent DMs; 24 stars,
stale), xmszm-memory (multi-user namespaces, file-backed).

**Memory platforms with MCP servers.** Mem0/OpenMemory, Supermemory,
Zep/Graphiti, Cognee, Letta, and Anthropic's official Knowledge Graph
Memory server (the reference implementation; the most-installed memory
MCP anywhere).

**Single-user but feature-rich.** docmancer (121 stars, the healthiest
independent project found), Lians, Amber, MnemoQ, SAE4U, Ragionex,
Memphora.

Traction check: among the rif-like group, only Basic Memory has real
adoption. Lore has 7 stars, Memlord/Sylex/bikky/Memphora are near zero,
SharedMemory.ai carries a D maintenance score on glama. The projects
with genuine traction are all single-user, developer infrastructure, or
engineering-team tools.

**Lore is the one to watch.** Self-hosted (Docker + Postgres/pgvector,
MIT, 305 commits), it is the only surveyed server that combines
private→shared visibility promotion, per-user scoping, workspaces with
RBAC and OIDC, audit logs, write-side PII redaction, bi-temporal facts
with supersession chains, and hybrid vector/full-text/graph recall. It
is rif's feature set re-imagined for engineering teams and coding
agents — no hosted offering, no human web app, no consent flow, no
consumer path. If anyone repositions toward households with a hosted
product, this architecture is the threat.

## What the field can do that rif cannot

1. **Semantic / hybrid search** — near-universal: Mem0, Basic Memory
   (with reranking), Amber (vector + FTS + rank fusion), docmancer
   (offline hybrid), Lore, bikky, Cognee, Graphiti. rif retrieves by
   index descriptions alone.
2. **Automatic capture** — Mem0 auto-extracts and compresses; Amber
   captures as you talk; bikky harvests facts from Claude Code and
   Copilot transcripts; Lore auto-stores via hooks. rif writes only
   deliberately.
3. **Hook-time injection** — Pathrule injects path-relevant team
   knowledge before the model's first tool call; Lore auto-injects in
   ~20 ms; docmancer bakes memory into always-loaded context. rif
   depends on the model choosing to call `load_index`.
4. **Knowledge-graph structure** — entities, relations, traversal
   (official server, Graphiti, Cognee, Lore, SharedMemory). rif stores
   prose pages.
5. **Temporal reasoning** — Lians answers point-in-time recall with
   fact lineage and conflict lists; Lore has valid-time vs system-time
   with supersession chains; Graphiti's whole model is temporal. rif
   keeps history but cannot answer "what did we believe on June 1st?"
   through a tool.
6. **Memory lifecycle** — decay, TTL, consolidation, contradiction
   resolution (Lore, bikky, MnemoQ's spaced repetition, Mem0's
   compression). rif pages live until someone edits them.
7. **Self-hosting / local-first** — most of the field runs on your own
   machine; Pathrule and Basic Memory offer it as a tier. rif is
   hosted-only. Sylex adds E2E encryption on top.
8. **Org machinery and developer APIs** — RBAC roles, SSO, audit logs,
   real-time co-editing (Basic Memory Teams), per-end-user memory SDKs
   (Mem0, Zep, Letta). rif has none of these.

## How rif beats them, gap by gap

The strategy is not to close all eight. Half of these "gaps" are the
product. The calls:

**Close — search, on the existing terms.** The one gap that will
genuinely hurt. Index-first holds while the index fits the context
budget — the spec's own escalation path ("index-plus-selective-read,
not embeddings") and the unmeasured phone context ceiling both point
the same way. The move that beats the field: **Postgres full-text
search as a `search_pages` tool, inside the same RLS session.** Every
competitor bolts retrieval onto a vector store with no per-person
guarantee — bikky shares a raw Qdrant collection; a forgotten filter
returns someone else's memories. In rif, a search that forgets a filter
returns *nothing*. "Search that cannot leak across spaces, by
construction" is a sentence no competitor can say, and it needs no
embeddings, no new infrastructure, and no revision of the
no-vector-database position. Embeddings stay refused until FTS
measurably fails.

**Close — cheap temporal reads.** The revision history already exists;
an `as_of` parameter on `read_page` is a small, honest feature that
matches Lians/Lore's headline capability for households ("what did the
plan say before we changed it?"). Low priority, high
capability-per-effort.

**Adapt — capture, without surveillance.** Auto-capture is the field's
answer to "writing memory is work," and for a household product the
literal version is disqualifying: a family assistant that silently
records is the creepy thing rif exists not to be. But the *labour
problem* is real. rif's answer is already half-built: `remember`
appends to inbox pages, and the spec's maintenance routine compiles
them. Finish that loop and frame it as **review-then-keep**: the
assistant proposes at conversation end, inbox pages stage, the Monday
compile promotes — a human sees everything before it becomes memory.
Deliberate stays the brand; the friction goes.

**Adapt — presence, without hooks.** Pathrule's real insight is that
memory must arrive without being asked for. rif's equivalent is
distribution, not architecture: the shipped agent skill already
instructs index-loading; add a Claude Code plugin/hook that calls
`load_index` at session start, and make the operating protocol do the
same for other surfaces. Same effect as hook-time injection, no new
server capability.

**Adapt — lifecycle as ritual, not decay.** Silent forgetting is wrong
for a memory humans co-own; a fact does not become false because nobody
mentioned it for a month. But staleness *surfacing* is right, and the
spec's maintenance pass (staleness sweep, cross-space contradiction
check, flag-never-resolve) is precisely the humane version of what
bikky and MnemoQ automate. Shipping that routine converts a spec
section into a differentiator: the competitors' lifecycle features
delete quietly; rif's asks.

**Refuse — knowledge graphs.** Triples are for machines; rif's readers
are people. A wiki humans actually read *is* the product. Wiki-style
links between pages give the useful fraction of graph structure without
abandoning prose. Anyone who wants entity extraction is not rif's
customer.

**Refuse — self-hosting, for now.** It serves the privacy-conscious,
but it forks scarce effort into packaging, support, and upgrade paths,
and rif's trust story is already load-bearing without it: RLS enforced
at the database, full export with history ("one-way, out — nothing
locks you in"), and invite-only intimacy. Revisit only if hosted-trust
objections actually block adoption. The export hatch is the honest
answer until then.

**Refuse — org machinery and SDKs.** RBAC, SSO, per-end-user memory
APIs serve markets rif is deliberately not in. Basic Memory and Mem0
can keep them. The moment rif grows roles and seat pricing it becomes
the eighth team-memory product instead of the only household one. (One
narrow exception worth watching: a read-only member — the accountant
who may read the `taxes` space but not write it — is a *household*
need, not an org one.)

## The standing moats, restated against this field

Nothing in the glama sweep touches: the two-step consent flow with
named readers; per-person privacy a database enforces rather than an
application promises; one person in several named spaces plus a private
space; the operating protocol as a first-class artifact; a web app a
non-technical person can read and edit. Lore has the architecture but
not the audience, the surface, or the hosting; Basic Memory has the
audience machinery but not the space model or the consent flow.

The durable play is therefore: ship RLS-scoped FTS before the corpus
ceiling bites, finish the inbox-compile loop and the maintenance
ritual, make the index arrive by default on every surface — and keep
refusing, loudly and in the README, the features whose absence *is* the
positioning: no silent capture, no silent forgetting, no un-sharing,
no seat pricing.
