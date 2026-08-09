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
