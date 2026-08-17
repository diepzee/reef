# Directory submissions — ready-to-paste copy

The competitor sweep's conclusion (see `docs/competitor-research.md`): while
the household position is open, being findable wins more than any feature.
Basic Memory's real advantage today is that people can find it. These are
the listings that close that gap, with the copy ready so each submission is
a paste, not a writing session.

## 1. Anthropic connector directory — the highest-leverage listing

Claude's directory is where Basic Memory, Mem0, and Supermemory get their
users. Every memory entry there today is single-person memory or a company
knowledge base; reef would be the only shared/household memory in the list.

**Where:** the connector directory intake — start from
<https://www.anthropic.com/partners/mcp> (form location has moved before;
if it moves again, search "submit connector Claude directory").

**Prerequisites to check before submitting:**

- [ ] A privacy policy page on reefwith.me the form can link to.
- [ ] A support contact address on the site.
- [ ] A decision on review access: reef is invite-only, and a directory
      reviewer cannot sign up. Either mint a reviewer invite as part of the
      submission, or state the invite-only model in the notes and offer to
      invite the review team. Do not soften the invite-only door for the
      listing — it is the positioning.

**Name:** reef

**Tagline (one line):** Shared, living memory for you and the people you
share your life with.

**Description:**

> Your assistant forgets you between conversations. reef gives it a memory
> that lasts — and one your household, your family, or your small circle
> can deliberately share. Everyone gets a private space only they can
> read; shared spaces hold exactly what members chose to put there.
> Memory is human-editable Markdown you can read, edit, and export — not
> an opaque blob. Sharing anything personal is a two-step consent flow
> that names every reader before it moves. Privacy is enforced by the
> database itself (PostgreSQL row-level security), search can only ever
> see what you could open anyway, and nothing is recorded silently: the
> assistant says what it keeps, and you can strike anything first.
> Invite-only, by design.

**Category:** Productivity / Memory.

**Server URL:** `https://reefwith.me/mcp` (remote MCP, OAuth via WorkOS
AuthKit, dynamic client registration — works on claude.ai, desktop, and
the mobile app as a custom connector today).

**Example prompts** (directories usually ask for 3):

1. "What do we know about the boiler?" — index-first retrieval plus
   full-text search across your spaces.
2. "Remember that Nora's swim class moved to Thursdays." — staged capture,
   announced before it lands, filed on the next tidy-up.
3. "What did my partner's assistant add to the household space this week?"
   — the whats_new activity surface.

## 2. glama.ai — claim and enrich the existing listing

reef is already listed as `diepzee/rif` (auto-indexed). Claiming it
requires the repo owner's GitHub account.

- [ ] Claim the listing (glama.ai → the server page → "Claim").
- [ ] Once the repo is public, glama scores license/quality/maintenance
      from it — the private repo is why the listing is thin today.
- [ ] Check the categorisation: Knowledge & Memory is right; "Note Taking"
      underplays it. Categories follow from the README and server.json
      keywords, so adjust `server.json` keywords if needed.

## 3. Everything else

Covered by `operator-checklist.md` steps 8–12: official MCP registry
(`mcp-publisher`, needs the `me.reefwith` DNS verification), mcp.so,
awesome-mcp-servers, Show HN. The Show HN and X drafts live in
`launch-drafts.md`.

One line worth adding to all of them now exists and did not when the
drafts were written: *search that cannot leak across spaces by
construction, point-in-time reads of any page, an activity feed of what
the other assistants wrote, and read-only members for the accountant.*
