# Reef's tools, built around workflows (RUG-40)

Date: 19 November 2026. Status: draft for Wouter's approval.

## Why

Reef offers connected assistants 31 tools. Most mirror the database: read,
write, edit, delete and list, for engrams, spaces, files and images. Weaker
models choose worse as that list grows, and none of the tools matches what a
person actually asks for. Wouter's principle (18 November 2026): **expose
useful workflows, not a full API.**

What Wouter uses Reef for today: looking up specific facts (insurance,
medical), getting advice grounded in his own history (training), and logging
new sessions. His `climbing-log.md` shows the shape of logging: 91 changes,
74,000 characters, every change about 800 characters longer than the one
before, never shorter. Today each new entry means the assistant rewrites or
patches the whole engram.

Reef is for individuals and for companies. A company's memory has three kinds
of writer: people, their assistants, and automated systems.

## Goals

1. One tool per workflow a person or an assistant comes to the memory for.
   About 15 instead of 31.
2. Every call says why it was made, in a `reason` the assistant fills in, so
   the set can be tuned from real use.
3. The renames ship in the same change: cove becomes **space**, page becomes
   **engram**, everywhere (decided 18 November 2026, see the glossary).
4. One breaking change for connected assistants, announced.

## Non-goals

- **Writers that are not people:** system identities, an HTTP capture
  interface, retry keys and caps per writer. That is the next project, and it
  overlaps with RUG-41.
- **Review,** finding what is out of date or contradicts itself. That builds
  on the dreaming tickets RUG-26, RUG-28 and RUG-39.
- **Episodes** (RUG-24). See "The call log and episodes" below.
- New search quality work. `recall` uses search as it is today.

## Anti-goals

This fails, even if it works, when:

- a person's private engram, query or reason reaches somebody else, a log or
  Logfire;
- an assistant can share, delete or change instructions without the person
  confirming;
- the tool count creeps back up, by keeping old tools beside new ones;
- a ChatGPT-style connector stops working.

## Approaches

| | Approach | Trade-off |
|---|---|---|
| A | Clean cut inside `server.py` | Quickest, but the logic stays tied to MCP; the HTTP interface of the next project would copy or untangle it |
| **B, chosen** | **Clean cut on a shared method layer** | About a day more now; each method is tested once, the reason log cannot be forgotten, the next project adds only thin HTTP shells |
| C | Gradual, new methods beside old tools | Data before cutting, but more than 31 tools for weeks, and two breaking changes instead of one |

Wouter chose B on 19 November 2026.

## The methods

Grouped by job. "Replaces" lists today's tools that disappear.

### Find

| Method | What it does | Replaces |
|---|---|---|
| `load_index()` | Start of every conversation: the operating protocol, the persona and the index of every engram and file, per space. Unchanged in purpose. | `load_index`, `get_operating_protocol`, `load_all_context`, `list_coves` |
| `recall(question, space=None, limit=5, reason)` | The passages that best answer a question, each with its engram, space, title, author, last change and a reference to open it. Uses hybrid search and, for large memories, the reranker. | `search_pages`, the common search-then-read pair |
| `brief(topic, space=None, entries=10, reason)` | Everything that bears on one topic: the related engrams, and for logs only their last `entries` entries. Made for "advise me" and "prepare me for". | new |
| `open(refs, as_of=None, reason)` | Whole engrams by space and path, or stored files by key. For when a passage is not enough. | `read_page`, `read_pages`, `read_file`, `read_image` |
| `catch_up(since=None, reason)` | What changed in the person's spaces, who changed it, when. | `whats_new` |

### Capture

| Method | What it does | Replaces |
|---|---|---|
| `remember(fact, space="personal", reason)` | A quick fact, added to the space's inbox. Unchanged. | `remember` |
| `log(space, engram, heading, entry, reason)` | Adds one entry at the end of an engram and changes nothing else. Creates the engram, tagged `log`, if it does not exist. The heading is written by the assistant, in the style of the log's earlier entries. When the entry would take the engram past its size cap, starts the next part instead; see "Logs in parts". | new |
| `write(space, engrams, message, reason)` | Creates or replaces whole engrams, several at once, each with an optional expected version. For new topics and restructures. | `write_page`, `write_pages` |
| `store_file(space, filename, data_base64, mime, description, engram=None, reason)` | Keeps a document or an image with a searchable description. | `add_file`, `add_image` |

### Maintain

| Method | What it does | Replaces |
|---|---|---|
| `correct(space, engram, old_text, new_text, message, expected_version=None, reason)` | Replaces one exact passage, which must occur once. History keeps the old text and who changed it. | `edit_page_section` |
| `undo(space, engram, version=None, reason)` | Restores the version before the last change, or a given one, as a new change. Nothing in history is lost. Cannot bring back a forgotten engram. | new |
| `forget(space, engram=None, file=None, confirm=None, reason)` | Permanent deletion, in two steps: the first call returns what would go and a confirmation token; the second, with the token, deletes. | `delete_page`, `delete_file`, `delete_image` |

### Share and instruct

| Method | What it does | Replaces |
|---|---|---|
| `share(engram, to_space, section=None, to_path=None, confirm=None, reason)` | Moves a personal engram, or one section, into a shared space, in two steps like `forget`. As today: the private original is replaced by a pointer to the shared copy, so nothing private exists twice. | `prepare_to_share`, `confirm_share` |
| `instruct(body, confirm=None, reason)` | Changes the person's persona: how their assistants work with them. Personal space only, as today. Two steps, because it steers every assistant the person uses. | `update_meta_page` |

### For connectors

| Method | What it does | Replaces |
|---|---|---|
| `search(query)` | The exact shape ChatGPT-style connectors require. No `reason`, or an optional one. | `search` |
| `fetch(id)` | The same. | `fetch` |

That is 16 methods.

### Leaves the assistant

Creating, renaming and deleting spaces, leaving one, inviting people to a
space or to Reef, and removing members: `create_cove`, `rename_cove`,
`delete_cove`, `leave_cove`, `invite`, `invite_to_reef`, `remove_member`.
All already work on the website. A company wants these controlled by people.

### Logs in parts

Decided by Wouter on 19 November 2026. An engram is capped at 200,000
characters, so it stays loadable. `climbing-log.md` is at 74,000 and grows
about 800 per entry, so it reaches the cap in about 150 entries.

- When an entry would take a log past the cap, `log` starts the next part,
  `climbing-log-2.md`, then `-3`, and so on. Each part is tagged `log`, opens
  with a link to the part before it, and the part before gets a link to it.
  No text ever moves.
- `brief` treats the parts of one log as one log and takes its last entries
  across them. `recall` searches all parts like any engram.
- Restructuring a log that has grown into parts, for example into an archive
  and a live log, is left to dreaming, which can look for logs in parts and
  clean them up. That is a follow-up for the dreaming tickets (RUG-28,
  RUG-39), not this change.

## Cross-cutting rules

### The method layer

- Each method is one async function in `reef/methods/`, independent of
  transport. It takes the principal, the `reason` and its own arguments, and
  returns plain data.
- One wrapper does what every method needs: the call log, the timing, the
  outcome, and who may call it.
- The MCP tools in `server.py` become one-line shells. The next project adds
  HTTP shells over the same functions.
- Every method keeps today's security: it runs in an armed transaction, row
  security decides what it sees, and model calls happen outside transactions
  as search does now.

### Staged actions

`share`, `forget` and `instruct` change what other people see, or destroy
something, or steer assistants. Each takes two calls: the first returns a
plain description of the effect and a short-lived token; only a second call
with that token acts. The protocol tells the assistant to show the effect to
the person and wait for a yes. This is how `prepare_to_share` and
`confirm_share` already work; it now covers three methods with one pattern
and one tool each.

### The `reason`

- Every method except `search` and `fetch` takes `reason`: why the call is
  made, in a few words, from the person's request. At most 200 characters;
  longer is cut, not refused.
- Required in the signature, but an empty reason still works, so a weaker
  model never fails because of it. The log counts empty reasons.
- The tool description says that the reason is logged privately for the
  person, to improve Reef.
- `recall` and `brief` may use the reason as extra context for the search.

### The call log

One row per call: person, session, method, reason, writer kind, duration,
how much it returned, outcome or error class, time.

- **Private to the person:** row security, as for the search log. Nobody
  reads another person's calls, operators included. Analysis is in totals, or
  each person on their own.
- **Kept 90 days**, a constant like the search log's, purged hourly by the
  worker.
- **In the person's data export.**
- **Never in Logfire or the app logs.** `reason` joins the telemetry scrub
  list.
- **Off until switched on** (`REEF_CALL_LOG`), after the privacy page says so
  and Nathalie and the third account have been told. Switching on gets its
  own ticket, per the rule that switch-on is part of done.
- **Writer kind:** `assistant` for MCP calls, `person` for the website,
  `system` reserved for the next project. Engram history records it too.

### The call log and episodes

Decision 4 of 17 November 2026 says Reef records each session itself as an
episode, kept forever and visible only to the person (RUG-24). The call log
is different: it serves tuning Reef, not the person's memory, so it is kept
90 days. RUG-24 may build episodes from the same calls, with its own
retention and rules.

### What stays out of this change

My calls, to keep the change to its purpose:

- **Instructions for a shared space.** `instruct` stays personal. Text in a
  shared space that addresses the assistant is treated as someone trying to
  steer it; that rule is what protects against prompt injection. Shared-space
  instructions need their own design.
- **The website.** It keeps its own routes and is not moved onto the method
  layer here. Its writes record the writer kind `person` in engram history;
  the call log covers assistants only. The next project, which adds HTTP
  shells, is the natural moment to move the website too.

## The renames

Cove becomes space, page becomes engram, everywhere, in one change:

| Where | Size, measured 19 November 2026 |
|---|---|
| Database | 6 tables, 14 columns, 5 security functions, 17 policies, 10 indexes |
| App code | about 2,600 lines in `src/reef` |
| Web app | about 1,200 lines in `frontend/src` |
| Site | about 125 lines |
| Tests | about 1,900 lines |

- **The database:** one migration, in one transaction, renaming tables,
  columns, indexes and policies, and recreating the security functions under
  their new names with the same bodies. Rehearsed first against a local
  database with production's schema and test data, never production's data.
  Merged on a quiet `main`; a short interruption during the deploy is
  accepted and announced in the pull request.
- **Code and text:** module, class and function names (`pages.py` becomes
  `engrams.py`, `Cove` becomes `Space`), user-facing text on the site and in
  the web app, the operating protocol, and the glossary.
- **Web addresses:** new paths use space and engram; old app paths redirect.
- **The export:** schema version 2, with `spaces` and `engrams`.
- **Environment variables** (my call): new names where they say page, such as
  `REEF_ENGRAM_MAX_CHARS` and `REEF_RERANK_MIN_ENGRAMS`, with the old names
  still read, as `RIF_` names are today. The settings name test pins both.
- **Old tool names:** a client that cached the old list gets an error naming
  the new method. Keeping old names callable but hidden may be possible
  through FastMCP's app-only tools, but that feature is marked as deviating
  from the MCP specification; the builder checks it and drops the idea if it
  does not work for ordinary clients. Listed aliases are out: they would
  bring the count back up.

## The operating protocol

`src/reef/protocol.md` is rewritten for the new methods and words. In
particular:

- start with `load_index`, answer questions with `recall`, use `brief` for
  advice and preparation, `open` only when passages are not enough;
- log sessions with `log`, never by rewriting the engram;
- show the effect of `share`, `forget` and `instruct` and wait for a yes;
- give a real reason with every call.

## Measuring it

- The tool count: 16 against 31.
- `recall` must find at least as well as search does now: 95% of the private
  questions in the first five on production data, measured read-only as on
  19 November 2026.
- `log` on `climbing-log.md` sends one entry, not the engram.
- Before merging: the automated tests, and each workflow run once through
  Claude Code against a local Reef, to confirm the intended method is picked.
  A ChatGPT-style connector can only be checked against production, so it is
  checked after release, in the ChatGPT app, by Wouter if he uses it.
- After switch-on, the call log shows which methods are used, which are not,
  and what assistants asked for that no method serves.

## Left to the builder

- The exact passages `recall` returns and how much text per passage, within
  today's section cutting.
- How `brief` chooses related engrams and its total size budget; how it
  recognises a log (the `log` tag, set by `log`; existing logs such as
  `climbing-log.md` get the tag in the migration if their path ends in
  `-log.md`).
- Token lifetime for staged actions, following today's share nonce
  (10 minutes).
- The order of work inside the change, as long as it ships as one release.
