# Test conversations: design

Date: 22 November 2026. Status: draft for Wouter's review.

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
2. **A loader** that puts that person into an account and checks, before each
   run, that the account still holds exactly that memory.
3. **Scenarios**: what the person says, and what a right outcome looks like.
4. **A run**: an Orca worker plays each scenario through Codex on the Astra
   model, against the local account or the production one.
5. **A report**: per scenario, which tools were called, whether the answer
   was right, and how long it took.

Runs happen when we ask for them: after a change to tools, search, the
protocol or file reading, and before calling such work done.

## Goals

- Every one of the 16 tools is used by at least one scenario.
- The same scenarios run against both targets, and their results can be
  compared.
- A run starts from the same memory every time.
- Tool use is read from a record, not from the assistant's own account of it.

## Non-goals

- Scheduled or automatic runs. Runs happen when asked.
- Retrieval numbers. The LongMemEval benchmark (`scripts/eval/`) measures
  those, locally.
- Conversations between two people (`catch_up` showing what someone else
  wrote). That needs a second made-up account; it can come later.
- Measuring speed. Durations are recorded but not judged.

## Anti-goals

These would make it a failure even if it ran:

- **Touching a real account.** No real data is read or written, ever.
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
- **Keeping it predictable:** a fixed memory plus a scratch area (below).

## Approaches considered

How to keep the production memory predictable while some scenarios write:

1. **Fixed memory plus a scratch area (chosen).** Writing scenarios write only
   under `scratch/`. Before each run the loader checks the fixed memory and
   restores anything that drifted; after it, it empties `scratch/`. Every tool
   is tested in production, and the memory stays the same.
2. **Wipe and reload before every run.** The most predictable, but slow, and
   each run adds history to the account.
3. **Read-only in production, writing tested only locally.** The simplest, but
   it misses production-only paths such as file storage.

## The parts

### 1. The made-up person

Kept under `tests/conversations/person/`, committed. Everything is invented:
no real person's name, address, employer or documents.

- About 30 to 40 engrams in Dutch and English: a profile, a household, work
  notes, a few how-tos, and some that link to each other.
- A training log with dated entries, for `log` and `brief`.
- A few files: a text PDF of several pages, a scanned page (an image of
  printed text) and a photo with a description. Scenarios that read text
  inside files only pass in production once file reading is on (RUG-60);
  until then they are reported as skipped, not failed.
- **Scans are read locally (my call):** the test account's spaces keep Gemini
  off, which is the default. The scanned page tests Reef's own reading, and
  reloading the person costs nothing. Revisit when Gemini reading ships.
- A persona: the instructions every assistant follows.
- A marker engram that says this is the test account. The loader refuses to
  write or delete anything in an account that lacks it.

The account also needs one shared space with only this person in it, so that
`share` has somewhere to move an engram.

### 2. The loader

A script that talks to Reef through the `reef` command-line tool, so it is
exact and repeatable. It never involves a model.

- **Load:** writes the made-up person into an empty account.
- **Check, before each run:** compares the account with the repository: the
  list of engrams, each engram's text, the files, and the persona. It restores
  anything that drifted, forgets anything outside `scratch/` that is not part
  of the person, and reports what it changed.
- **Clean, after each run:** forgets everything under `scratch/` and in the
  shared space, and restores the persona.
- **Guard:** before any write or delete it checks for the marker engram and
  stops if it is missing. This protects against the tool being signed in to a
  real account by mistake.
- **Files (my call):** the tools cannot list an account's files, and file keys
  are random per target. The loader keeps the keys it stored in a small state
  file per target, outside git (`~/.reef-conversations/<target>.json`), and
  compares file counts from `load_index`. A file a scenario stores is found
  from the `store_file` result in the Codex session record, and the clean step
  forgets it. If a count is off and the key is unknown, the loader stops and
  says so; Wouter removes the file on the website. No server change.

### 3. Scenarios

Kept in `tests/conversations/scenarios.yaml`, committed. Each scenario holds:

- what the person says, as one or more messages;
- whether the person says yes when the assistant asks to confirm (for
  `forget`, `share` and `instruct`);
- the tools that must be called, and any that must not;
- the facts a right answer contains, and the engrams it must name;
- for writing scenarios, what the memory must hold afterwards (checked by the
  loader);
- whether it needs file reading.

Writing scenarios use `scratch/` paths only. The two exceptions restore
themselves: `instruct` changes the persona and `share` moves an engram from
`scratch/` into the shared space; the clean step undoes both.

### 4. The run

Started with `orca orchestration worker-start --worktree new-child --agent
codex --model gpt-6-astra`, with the target (local or production) in the task.

- The worker runs the loader's check, then plays each scenario in a **fresh
  Codex session** (`codex exec`, Astra), so one scenario's answer cannot leak
  into the next and each one starts the way a real conversation does. The
  person's messages are the only prompt; the assistant has only the Reef tools.
- Codex records every tool call in its session record. The worker keeps those
  records; tool use is read from them.
- The worker runs the loader's clean step at the end, also after a failure.

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
- The report lists, per scenario: pass, fail, skipped or for review; the tools
  called in order; and the duration. It ends with totals per target.
- Reports hold only made-up content. They are saved outside git, and a summary
  goes on the ticket of the change being tested.

### 6. The production account

- **Left out of totals:** a person can be marked as a test account. The call
  log totals and the search-log statistics skip marked people. The mark is set
  once in production, with Wouter's say-so.
- **One-time setup, by Wouter:** create the account with an address of his
  choosing; create its shared space; sign it in once in Codex and once in the
  `reef` command-line tool. Then the loader loads it.
- The local account is a new local person, `conversations@reef.local`, so the
  existing `test@reef.local` slice stays as it is.

## Constraints

- Only made-up content, in the repository and in production.
- No credentials in the repository, in `.env`, or handled by an assistant.
- The loader's guard runs before every write and delete.
- File-reading scenarios skip in production until RUG-60 is done.
- The LongMemEval rule in `scripts/eval/README.md` stands: that data never goes
  to production. This set is separate and small.
- Text a person reads (scenario messages, the persona, the report) follows
  plain language.

## Left to the builder

- The made-up person's name and the exact engrams, files and scenarios, within
  the rules above. About 25 to 35 scenarios is enough.
- How the two Codex servers are configured without reaching Wouter's own Codex
  sessions (a separate Codex home, a profile, or per-run settings).
- How the local server is started for the local target.
- The report's file format.
- How a person is marked as a test account, and the migration for it.
