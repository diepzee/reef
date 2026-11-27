# Test conversations: design

Date: 22 November 2026. Status: for Wouter's review.

## Why

Unit tests and code reviews show that each tool does what its code says. They
do not show whether an assistant, given Reef, picks the right tools and gives
the person a right answer. A conversation shows that.

Wouter wants these conversations run on a made-up person, both on this
machine and in production. Production holds a fixed reference memory that is
safer and more predictable than a local database, which gets dropped and
rebuilt. Production also covers what local cannot: the deployed services, real
search, file storage and sign-in.

## What it is

1. **A made-up person**, kept in the repository: engrams, files and a persona.
2. **A reset** in Reef's server that wipes a test account and loads the
   made-up person into it. It works only for accounts flagged as test
   accounts.
3. **Scenarios**: what the person says, and what a right outcome looks like.
4. **A run**: an Orca worker plays each scenario through Codex on the Astra
   model, against the local account or the production one.
5. **A report**: per scenario, which tools were called, whether the outcome
   was right, and how long it took.

Runs happen when we ask for them: after a change to tools, search, the
protocol or file reading, and before calling such work done.

## Goals

- Every one of the 16 tools is used by at least one scenario.
- The same scenarios run against both targets, and their results can be
  compared.
- Every run starts from the same memory, and so does every writing scenario.
- Assistants write where they naturally would.
- Tool use is read from a record, not from the assistant's own account of it.

## Non-goals

- Scheduled or automatic runs. Runs happen when asked.
- Retrieval numbers. The LongMemEval benchmark (`scripts/eval/`) measures
  those, locally.
- Exactly identical output between runs. The model varies more than any
  difference in IDs or timing would; results are judged per scenario, as pass
  or fail.
- Conversations between two people (`catch_up` showing what someone else
  wrote). That needs a second made-up account; it can come later.
- Judging speed. Durations are recorded but not judged.

## Anti-goals

These would make it a failure even if it ran:

- **Touching a real account.** No real data is read, written or deleted, ever.
- **Skewing production's numbers.** The test account must not count in the
  call log totals or the search-log statistics.
- **Drift.** A memory that slowly changes between runs makes results
  incomparable.
- **Trusting the assistant's word.** A report built from what the worker says
  it did.

## Decided

- **Runner:** an Orca child worktree with a Codex worker on the latest Astra
  model (`gpt-6-astra`), started through Orca orchestration.
- **Targets:** a local account and a production account, both holding the same
  made-up person.
- **Production sign-in:** Wouter creates the account and signs it in once.
  There are no passwords: Reef signs people in with Google or an emailed code,
  and no assistant enters credentials in production. Codex and the `reef`
  command-line tool each keep their own saved session after that one sign-in.
  When a session runs out, Wouter signs in again. Machine sign-in (client
  credentials) is left for RUG-41.
- **Keeping it predictable:** a reset in Reef's server, restricted to flagged
  test accounts, that wipes the account and reloads the made-up person (below).

## Approaches considered

**First round,** on keeping production predictable while some scenarios write:

1. Fixed memory plus a scratch area: writes only under `scratch/`, the rest
   checked and restored. Chosen first, then dropped: a review showed
   assistants cannot be kept inside `scratch/` without making the tests
   unrealistic (`remember` writes to the inbox, `log` to an existing log, and
   files get random keys).
2. Wipe and reload before every run. First rejected as slow and as adding
   history. The second point was wrong: forgetting an engram deletes its
   history, links, usage records and search vectors with it.
3. Production read-only, writing tested only locally. Misses production-only
   paths such as file storage.

**Second round,** after a code review by Claude and an independent one by
Astra. Both found that a run changes much more than text: history, links,
files of any state (including failed uploads the tools cannot see), usage
records and the activation built from them, search vectors, and queued
background work.

1. **A server reset that reloads the made-up person (chosen).** Complete,
   because only the server sees everything a person has. Safe, because the
   server checks a flag only an operator can set. It loads through Reef's
   normal saving code, so it keeps working when tables are added.
2. **An exact snapshot restored before every scenario, keeping every ID and
   file key.** The most exact, and the most code. The snapshot must be updated
   whenever Reef adds a table, and a missed table breaks it silently. The
   exactness buys little, since the model varies more than IDs do.
3. **Restoring from outside, through the tools.** No server code, but it
   cannot find every file, and it would have to trust a marker engram, which
   anyone can write, before deleting things.

## The parts

### 1. The made-up person

Kept under `tests/conversations/person/`, committed, and shipped with the
server so the reset can load it. Everything is invented: no real person's
name, address, employer or documents.

- About 30 to 40 engrams in Dutch and English: a profile, a household, work
  notes, a few how-tos, and some that link to each other.
- A training log with dated entries, for `log` and `brief`.
- A few files: a text PDF of several pages, a scanned page (an image of
  printed text) and a photo with a description. Scenarios that read text
  inside files pass in production only once file reading is on (RUG-60);
  until then they are reported as skipped, not failed.
- A persona: the instructions every assistant follows.
- A version number, raised whenever the person changes, and named in every
  report.

### 2. The reset

An operation in Reef's server, called through the test account's own signed-in
session (from the `reef` command-line tool). It is never offered to
assistants as a tool.

**Who may use it.** It refuses unless all of these hold:

- the signed-in person is flagged as a test account. Only an operator sets
  the flag, directly in the database; no tool, page or request can set it;
- the person is a member of no space someone else owns, and none of their
  spaces has another member.

**What it wipes**, for that person only: every engram in their spaces (which
removes history, links, usage records, activation and search vectors with
it), every file in any state with its stored bytes, pages and vectors,
pending shares, and their queued background work. A confirmation token issued
before the reset must not work after it.

**What it keeps:** the person, their sign-in sessions, their spaces and
memberships, and their search and call logs. The logs do not affect what tools
return; they are left out of totals and deleted after 90 days as usual.

**What it loads:** the made-up person shipped with the server, through the
same code an assistant's writes go through.

**When it is done:** it answers only once search vectors and file text are
ready, or says it timed out. A run never starts on a half-ready memory.

Locally, the same reset runs for `conversations@reef.local`, a new local
person, so the existing `test@reef.local` set stays as it is.

### 3. Scenarios

Kept in `tests/conversations/scenarios.yaml`, committed. Each scenario holds:

- what the person says, as one or more messages;
- whether the person says yes when the assistant asks to confirm (for
  `forget`, `share` and `instruct`);
- the tools that must be called, and any that must not;
- the facts a right answer contains, and the engrams it must name;
- for writing scenarios, what the memory must hold afterwards;
- whether it needs file reading.

Assistants write wherever they naturally would. Reading scenarios come first,
always in the same order. Each writing scenario starts with a reset.

### 4. The run

Started with `orca orchestration worker-start --worktree new-child --agent
codex --model gpt-6-astra`, with the target (local or production) in the task.

1. Reset.
2. Play the reading scenarios in their fixed order.
3. For each writing scenario: reset, play it, then read the memory with the
   `reef` command-line tool to check what it must hold.
4. Reset at the end, so the account rests as the made-up person.

Each scenario is played in a **fresh Codex session** (`codex exec`, Astra), so
one scenario's answer cannot leak into the next. The person's messages are the
only prompt; the assistant has only the Reef tools. Codex records every tool
call in its session record, and tool use is read from there.

The Codex sessions reach Reef through two servers that exist only for these
runs: `reef_local` (the local account, no sign-in) and `reef_test`
(production). Wouter's own Codex sessions must not see either.

**`search` and `fetch` (my call):** with all 16 tools offered, an assistant
rightly prefers `recall` and `open`. The scenarios for `search` and `fetch`
run with the server limited to those two tools by a per-run Codex setting
(`enabled_tools`). That uses the same server entry, so no extra sign-in.

### 5. Scoring and the report

- A script scores what can be checked exactly: tools called, facts present,
  engrams named, the memory's state after writing scenarios.
- What a script cannot judge (a fact phrased differently, a refusal that was
  right) is marked for review, and the controller judges it from the answer.
- Each report names the made-up person's version, Reef's version, and whether
  search ran with all its services or fell back. Random tokens and links are
  left out, so reports can be compared.
- Per scenario: pass, fail, skipped, for review or invalid; the tools called
  in order; the duration. Totals per target at the end. A refused bearer
  sign-in makes the run invalid, even when other checks fail.
- Reports hold only made-up content. They are saved outside git, and a summary
  goes on the ticket of the change being tested.

### 6. The production account

- **Left out of totals:** the call log totals and the search-log statistics
  skip people flagged as test accounts. Today's totals have no such
  exclusion, so this is new.
- **One-time setup, by Wouter:** create the account with an address of his
  choosing; create its shared space with no other members; sign it in once in
  Codex and once in the `reef` command-line tool. Then the flag is set in
  production, with his say-so, and the first reset loads the person.
- **Gemini:** reading scans with Gemini (RUG-37 phase 2) is not built yet.
  When it is, the test account's spaces keep it off, so scans are read by
  Reef's own reading and resets cost nothing.

## Constraints

- Only made-up content, in the repository and in production.
- No credentials in the repository, in `.env`, or handled by an assistant.
- The reset checks the flag and the membership rule on every call.
- Server changes, the flag in production and every merge need Wouter's
  approval.
- File-reading scenarios skip in production until RUG-60 is done.
- The LongMemEval rule in `scripts/eval/README.md` stands: that data never goes
  to production. This set is separate and small.
- Text a person reads (scenario messages, the persona, the report) follows
  plain language.

## Left to the builder

- The made-up person's name and the exact engrams, files and scenarios, within
  the rules above. About 25 to 35 scenarios is enough.
- How the reset is reached (for example a web API route that accepts the
  signed-in session), how confirmation tokens are made invalid, how readiness
  is detected, and the time limit.
- How the flag is stored, and the migration for it.
- How the two Codex servers are configured without reaching Wouter's own Codex
  sessions (a separate Codex home, a profile, or per-run settings).
- How the local server is started for the local target.
- The report's file format.
