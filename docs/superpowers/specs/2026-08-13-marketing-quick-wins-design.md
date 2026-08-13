# Marketing quick wins — design

**Date:** 2026-08-13
**Branch:** `marketing-quick-wins`

## Goal

Make reef discoverable and legible to its primary target, at near-zero cost,
without building any new product surface.

## Primary target

**Technical hobbyists and their circles.** The Claude-Code-using, HN-reading
hobbyist discovers reef through technical channels, does the MCP setup
themselves in five minutes, and then invites their circle — partner, family,
school run, accountant. The buyer is a hobbyist; the payload is their circle.

Rationale: cold consumers never land on reefwith.me (no consumer channel, and
no sign-up for them by design). The two real visitor types are invitees (who
arrive with a human guide) and curious technical people (from HN, MCP
registries, X). Onboarding demands a paid Claude plan and connector setup — a
non-technical person doesn't do that alone; someone sets it up for them. The
invite mechanic (5 per member per 30 days, relayed personally) already assumes
adoption spreads through a technical person's circle.

Secondary framing kept intact: reef remains invite-only consumer software for
households and small circles. We are choosing who the *door* is, not changing
what the product is for.

Deferred: a TypeScript/npm CLI. Real backlog item (npx-installable, reaches
Node-based agent sandboxes) but it duplicates the OAuth flow and creates a
permanent two-CLI sync tax. Revisit once outsiders actually use the Python CLI.

## Work items

### 1. Landing page restyle (`site/index.html`)

Direction: **warm story, proud tech.** The page speaks to someone MCP-literate
but sells the household outcome.

- Hero: keep "Memories you grow together" and the warm sub-line. The micro-line
  may keep MCP vocabulary — the target reader speaks it.
- Cove scenario sections (household / school run / accountant / trip / just
  you): make each concrete with example page content a cove would actually
  hold (e.g. "Emma's allergy list, the plumber's number, what we decided about
  the loft"). Frame as "what you set up for your people."
- Technical substance is a selling point, not a footnote: RLS as the privacy
  boundary, "a wiki, not a memory blob", index-first Karpathy-pattern
  retrieval. This reader evaluates products on exactly these.
- CLI + agent skill keep good billing (this reader wants them).
- Plain-language pass throughout (ISO 24495: reader-first, one idea per
  sentence, everyday words where jargon isn't load-bearing).
- Visual identity untouched: palette, coral mark, scroll structure, dark mode.

### 2. Repo & install fixes

- Make `github.com/diepzee/rif` public. Pre-flight done: gitleaks scanned all
  192 commits, no leaks. Flipping visibility is the owner's manual click or an
  explicit `gh` call — done as its own reviewed step.
- Set GitHub repo description, homepage (`https://reefwith.me`), and topics
  (mcp, mcp-server, memory, claude, ai-memory, …). Add a social preview image.
- Publish the CLI to PyPI so the advertised install works for people without
  repo access. Pick an available package name (bare `reef` likely taken);
  update README + site setup copy to match the new install command.

### 3. Distribution seeds

Submit reef to, in order of value:

- The official MCP registry (registry.modelcontextprotocol.io)
- mcp.so, glama.ai/mcp
- PR to punkpeye/awesome-mcp-servers

All listings point at `https://reefwith.me/mcp` and the site. These reach the
primary target where they already look for tools. Requires the repo to be
public first.

### 4. Show HN / X launch draft

Draft (not post) a Show HN and an X thread built on the invite-only story:
"there is no sign-up — the only door is a person." Angle: shared long-term
memory for your household's AI, private by database architecture, readable
Markdown you can leave with. Wouter posts when ready.

## Not building

- Waitlist, sign-up form, pricing page (invite-only stance is deliberate and
  is itself the story).
- TypeScript CLI (backlog, see above).
- New product features of any kind.

## Success criteria

- A stranger from HN can: read the site, understand who it's for in ten
  seconds, install the CLI with one working command, and hit the invite-only
  door with the story intact.
- Repo is public with metadata that makes it legible in search and lists.
- reef is listed in at least the official MCP registry plus one directory.
- Wouter has a launch post he'd actually publish.
