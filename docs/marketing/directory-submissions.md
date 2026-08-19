# Directory submissions — ready-to-paste copy

The competitor sweep's conclusion (see `docs/competitor-research.md`): while
the household position is open, being findable wins more than any feature.
Basic Memory's real advantage today is that people can find it. These are
the listings that close that gap, with the copy ready so each submission is
a paste, not a writing session.

**Two Anthropic directories, not one.** The Connectors Directory lists MCP
servers and reaches every Claude surface, but submitting to it needs a paid
Team or Enterprise organisation. The plugin directory reaches Claude Code and
Cowork, and an individual can submit to it for free. They are separate
shelves with separate audiences; §1 and §2 below cover each.

## 1. Anthropic Connectors Directory — the widest reach, and the paid one

Claude's directory is where Basic Memory, Mem0, and Supermemory get their
users. Every memory entry there today is single-person memory or a company
knowledge base; reef would be the only shared/household memory in the list.
It is also the only listing eligible for **Suggested Connectors**, Claude's
in-chat recommendations, with usage-based ranking.

**Where:** the submission portal at
<https://claude.ai/admin-settings/directory/submissions/new>. Guidance lives
at <https://claude.com/docs/connectors/building/submission> and the
pre-submission checklist at
<https://claude.com/docs/connectors/building/review-criteria>. Ignore the
Google Form that third-party blog posts still circulate — the official route
for a remote server is the portal, and the separate form is for MCPB desktop
extensions, which reef is not.

**The blocker, confirmed against the live account on 18 August 2026:** the
portal answers "You don't have access to organization settings. Organization
settings are available on Claude Team and Enterprise plans." Submitting needs
a **Team or Enterprise org**, with the rights sitting with an Owner by
default, and Team starts at two seats. This is a plumbing constraint rather
than a quality bar, but there is no individual route to this directory.

**Prerequisites:**

- [x] A privacy policy the form can link to — `site/privacy.html`, checked
      against the six areas review demands (collection, use, storage,
      third-party sharing, retention, contact).
- [x] A support contact on the site — `wouter@rugvin.be`.
- [x] Tool annotations. Every tool carries a title and a read-only or
      destructive hint; `tests/test_tool_annotations.py` fails if a new one
      arrives without them. Missing annotations is the most common rejection.
- [ ] **A populated test account.** "Empty or unrealistic test accounts" is
      an explicit rejection trigger: reviewers want sample records for every
      operation and stable IDs matching the example prompts below. The open
      door means a reviewer can now sign up unaided, but they would land in
      an empty cove and see nothing work. Seed a demo account with invented
      content — never a real household's — and hand over its credentials.
- [ ] **A public documentation page**, required by publish date. A help
      article covering setup, auth, example prompts, and limits is enough.
      reefwith.me has no such page yet.
- [ ] An icon, and a URL slug. **The slug is permanent once published.**

**Name:** reef

**Tagline** — the portal caps this at **55 characters**, which the long-form
line below overruns. Use one of:

> Shared, living memory for you and your people.

> Memory your assistant keeps and your people share.

**Description** (portal cap: 2,000 characters):

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

**Category:** Productivity / Memory (the portal takes one to five).

**Server URL:** `https://reefwith.me/mcp` (remote MCP, streamable HTTP,
OAuth 2.0 with dynamic client registration — reef is its own authorisation
server, which is the supported DCR mode).

**Example prompts** (directories usually ask for 3):

1. "What do we know about the boiler?" — index-first retrieval plus
   full-text search across your spaces.
2. "Remember that Nora's swim class moved to Thursdays." — staged capture,
   announced before it lands, filed on the next tidy-up.
3. "What did my partner's assistant add to the household space this week?"
   — the whats_new activity surface.

The portal also asks for use cases, what a user needs before connecting,
company details, data-handling answers, and seven policy acknowledgements.
Escalations go to `mcp-review@anthropic.com`.

## 2. Anthropic plugin directory — free, and open to an individual

Reaches Claude Code and Cowork, where it is surfaced as the
`claude-plugins-official` marketplace. Its audience is the technical
hobbyist who installs first and invites their household after — which is
reef's stated marketing target, so this shelf is not a consolation prize.

**Where:** two forms, and the second is the one that matters here.
<https://claude.ai/admin-settings/directory/submissions/plugins/new> needs
the same Team or Enterprise org as §1, but
<https://platform.claude.com/plugins/submit> accepts a Console account, and
the docs say plainly that individual authors who are not in a Team or
Enterprise organisation can sign up for Console and submit there. Console
accounts are free. Signing in there means accepting Anthropic's Commercial
Terms, so it is a step only Wouter can take.

**What is submitted:** a GitHub link. The repo must be public — closed
source is not accepted — which reef has been since 13 August 2026.

**What exists in this repo:**

- `plugins/reef/` — the plugin: the remote connector in `.mcp.json`, plus
  `/reef:recall`, `/reef:remember`, and `/reef:whats-new`.
- `.claude-plugin/marketplace.json` — the `haai` marketplace, so anyone can
  install today without waiting for a review: `claude plugin marketplace add
  diepzee/rif && claude plugin install reef@haai`.
- Both pass `claude plugin validate --strict`.

The plugin's version is stamped by `scripts/stamp_version.py` along with the
server and both clients, so it can never drift from the server it connects to.

**One caveat worth knowing before submitting:** the plugin docs encourage
bundling connectors that are already in the Connectors Directory, and warn
that others draw more warnings for users and are less likely to be verified.
Shipping the plugin first is still right — it costs nothing and it is where
reef's people are — but §1 is what removes that warning later.

**Licensed:** the server is AGPL-3.0-or-later; the clients and this plugin
are MIT, which is what both client manifests had already been claiming on
PyPI and npm without a LICENSE file behind them. See the README's Licence
section.

## 3. glama.ai — claim and enrich the existing listing

reef is already listed as `diepzee/rif` (auto-indexed). Claiming it
requires the repo owner's GitHub account.

- [ ] Claim the listing (glama.ai → the server page → "Claim").
- [ ] Once the repo is public, glama scores license/quality/maintenance
      from it — the private repo is why the listing is thin today.
- [ ] Check the categorisation: Knowledge & Memory is right; "Note Taking"
      underplays it. Categories follow from the README and server.json
      keywords, so adjust `server.json` keywords if needed.

## 4. Everything else

Covered by `operator-checklist.md` steps 8–12: official MCP registry
(`mcp-publisher`, needs the `me.reefwith` DNS verification — note this is a
registry requirement only; neither Anthropic directory asks for domain
proof), mcp.so, awesome-mcp-servers, Show HN. The Show HN and X drafts live
in `launch-drafts.md`.

One line worth adding to all of them now exists and did not when the
drafts were written: *search that cannot leak across spaces by
construction, point-in-time reads of any page, an activity feed of what
the other assistants wrote, and read-only members for the accountant.*
