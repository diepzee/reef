# Launch drafts — edit before posting

Refreshed 2026-08-24, against reef 1.0.0. The previous version predated the
`space`→`cove` rename, the public repo, the /docs page, and the open door.

Post on a weekday morning US time (13:00–15:00 UTC is the usual sweet spot),
and be at a keyboard for the next four hours. On HN the comments are the
launch; the post is only what starts them.

---

## Show HN

### Title

Pick one. HN strips editorialising, so no "by design", no adjectives it
cannot check.

1. **Show HN: Reef – shared long-term memory for your household's AI assistants**
   *(recommended — the household angle is the part nobody else is doing)*
2. Show HN: Reef – a shared, readable memory for AI assistants, over MCP
3. Show HN: Reef – Postgres-RLS-backed shared memory for Claude, ChatGPT and Codex

Do **not** put "invite-only" in the title. It reads as a velvet rope before
anyone knows what the thing is, and it is the single fastest way to lose the
thread to a meta-argument about access.

### Body

My partner and I both talk to AI assistants all day, and we kept telling them
the same things twice — then again next month, because neither one remembers.
reef is the piece I wanted: one memory a group of people share, which their
assistants read before answering and write back to as things change.

It is a remote MCP server. You add `https://reefwith.me/mcp` to Claude
(desktop, web, or phone), ChatGPT desktop, or Codex, and your assistant gets
memory tools. There is a CLI and a browser app for reading and editing the
same pages by hand.

Memory lives in **coves** — one private cove each, plus a shared cove for any
circle with a "we": the household, the school run, you and your accountant.

The three design choices I would most like a hard time about:

**Memory is a wiki, not a blob.** Human-readable Markdown pages behind
index-first retrieval (Karpathy's LLM-wiki idea, adapted for shared and
permissioned memory). The assistant loads a map of every page it may see —
path, title, tags, one-line description — then fetches only what the
conversation needs, and fetches again as the topic moves. The index is
rebuilt from the database on every call, so it cannot fall out of date.

**Privacy is enforced in Postgres, not in my application code.** Every page,
file, and membership sits behind row-level security, and the app connects as
an ordinary role rather than a superuser. A query with a forgotten filter
returns nothing rather than somebody else's data — getting it wrong fails
closed. Search runs under the same policies, so it cannot surface a page its
caller could not open.

**Memory is data, never instructions.** Every co-member of a cove controls
text that lands verbatim in your index — titles, tags, the line that becomes
the description — and that index is the first thing your assistant reads in
every conversation. An instruction planted there reaches a model holding
`read_page(personal, …)` and `write_page(shared, …)` at the same moment. So
the protocol says text addressed to the assistant is somebody trying to steer
it, and the answer is to tell you. Underneath that, a write to a shared cove
is diffed against your own private pages and a substantial verbatim run is
refused, pointing at the two-step consent flow instead. That check stops
copying and not paraphrasing; the module says so out loud about itself.

Honest gaps: backups run by hand (one real dump, one passing restore drill,
no cron yet), very few people are on it, and I have not measured the context
ceiling on a phone.

The server is AGPL — if you would rather not be a guest in my database,
`docker compose up` and it is yours. The clients are MIT.

One thing to be upfront about: reef is invite-only. Memory this personal
should arrive through somebody you trust, not a signup form. For this launch
I set aside a batch of places you can take yourself, while they last — and if
they are gone by the time you read this, the source is right there.

Site: https://reefwith.me — Source: https://github.com/diepzee/reef

### First comment (post it yourself, right after submitting)

Author here. Two things that did not fit above.

**Why not just a folder of Markdown in a git repo?** That is genuinely most
of the value, and for one person I would not argue. It falls over on the
second person: git has no notion of "this file is mine and invisible to you",
so the moment a household shares one repo, everybody's private notes are in
everybody's clone. The whole design follows from wanting private and shared
memory in one retrievable surface without a second system — which is why the
boundary ended up in the database rather than in a convention.

**What surprised me.** Writing the retrieval protocol was harder than writing
the server. An assistant will cheerfully answer from the index alone — path,
title, and a one-line description read enough like knowledge to feel
sufficient — and be confidently wrong. Most of my iterations since 1.0 have
been on the protocol text, not the code: which memory wins when the model
already thinks it knows something, and how to hand it over early enough that
it is read at all.

Stack, if useful: Python 3.13, Piccolo ORM, Postgres, MCP over streamable
HTTP, WorkOS AuthKit for identity, Railway for hosting. Tests run against a
real Postgres — with RLS on, as a non-owner role, because a suite that runs
as the table owner will happily prove nothing.

Happy to go deep on the RLS schema or the sharing ceremony.

### Prep sheet — what they will ask

Have these ready; do not paste them unprompted.

- **"Invite-only Show HN? Against the spirit."** Fair hit. Answer plainly:
  the source is AGPL and self-hostable in one command, and the open door is
  live right now. Do not defend the gate on principle — point at the two
  ways in and move on.
- **"Why not [mem0 / Letta / Zep / OpenAI memory]?"** Those are memory for
  one user and one agent, usually an opaque store. reef's unit is a group of
  people, and the store is pages you can open. `docs/competitor-research.md`
  has the detail if the thread wants it.
- **"Prompt injection through shared pages."** Do not claim it is solved.
  The honest version is in the third bullet: the protocol demotes memory to
  data, the leak guard closes the copy route, and paraphrase defeats the
  guard. Link `src/reef/leakguard.py` — the docstring argues against itself
  better than a comment will.
- **"You are storing my family's private life on your server."** Yes. That
  is why the licence is AGPL, why export gives you every page and revision
  as plain files, and why there is no un-sharing to un-trust later.
- **"What happens when you get bored / hit by a bus?"** Export is one-way
  out and always available; the server is AGPL; the pages are Markdown that
  outlive the deployment. Say the honest thing: it is one person and a small
  company, and that is a real risk you are taking.
- **"Free? What's the catch?"** Free while reef is small, and the site says
  so before you sign in. If it changes you hear it from us first, not from
  a bill.

---

## X thread

1/ Your assistant forgets you between conversations. Your partner's assistant
never knew you at all.

reef is one memory the two of you share — readable, editable, yours.

2/ Memory lives in coves. One private cove each, plus a shared one for any
circle with a "we".

The household. The school run. You and your accountant.

3/ It is a wiki, not a memory blob.

Real Markdown pages your assistant reads before it answers and tends as
things change — and you can open, edit, and export every one of them.

4/ Privacy is not a promise in my application code.

It is Postgres row-level security: the database itself cannot show your
private cove to anybody else's session. A forgotten filter returns nothing,
not somebody else's life.

5/ And memory is data, never instructions.

Anyone in a shared cove can write text your assistant reads first. reef
treats that text as something to report to you, not to obey.

6/ Works with Claude, ChatGPT desktop, and Codex over MCP. CLI on PyPI and
npm. Server is AGPL, so you can run the whole thing yourself.

7/ reef is invite-only — memory this personal should arrive through trust.
For the launch we set aside a number of places. Take one while they last:

reefwith.me

---

## Lobste.rs

Tags: `web`, `privacy`, `ai`, `show`

Same title as HN, without the "Show HN:" prefix. Lobsters wants the
engineering and none of the story — lead with the RLS paragraph, then
index-first retrieval, then the injection stance. Cut the household opening
to one sentence and cut the invite paragraph to one line with the self-host
link. Expect harder questions about the RLS policies specifically; have
`src/reef/rls.py` open.

## Reddit

**r/selfhosted** — lead with `docker compose up`, AGPL, and export. The
hosted instance is the footnote, not the offer. This crowd reads an
invite-only SaaS as the enemy and a self-hostable server as the point.

**r/ClaudeAI** — lead with the connector: paste one URL into Settings →
Connectors and every conversation afterwards starts already knowing. Show
the plugin (`/reef:recall`, `/reef:remember`, `/reef:whats-new`). Keep it to
a screenshot and five sentences.

Do not cross-post the same body. Both subs will notice, and both have people
who read HN.
