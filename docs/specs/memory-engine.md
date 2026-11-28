# Reef's memory engine: the design

**Status:** canonical since 28 November 2026 (RUG-42). This is the one design
document for the memory engine. Where an older document says something else,
this one wins. Section 9 says what it replaces.

**State described:** release 2.7.1, 27 November 2026.

**Terms** follow `docs/glossary.md`: space, engram, person, assistant, link,
association, activation, dreaming. Code names appear in `code style`. A few
of them are older than the glossary: the table for activation is
`engram_heat`, and the table for files is `attachments`.

**How to read this.** Section 2 is what is built. Section 3 is what is
planned, part by part; section 4 gives the order. Every decision carries
its date, so a reader can see which one is the latest. Section 6 lists
what is still open.

## 1. What the engine is for

Reef is a memory that a person shares with their assistants. A person can
use it from any model that speaks MCP. So the engine, not the model's
cleverness, has to find the right engram.

The design has two layers.

1. **Finding.** A search that finds an engram when the wording differs from
   the question, in English and Dutch, and that reads inside stored files.
   This layer is built.
2. **Self-organising.** Use shapes what comes up first. Engrams used
   together become associated. A nightly dream reorganises one person's
   engrams without asking. This layer is planned; a small part of its
   groundwork is built.

### Rules that hold everywhere

These apply to everything below, built or planned.

1. **The database enforces who sees what.** Every read and write runs as
   the person, under row-level security. No running process holds a
   credential that bypasses it. The worker arms itself as the person each
   job acts for.
2. **What a person does is private to that person.** Usage, activation,
   associations, learned cues, episodes and the search log are visible only
   to their owner, also in a shared space. One member's reading habits are
   not another member's business.
3. **Engram text stays inside Reef.** Embedding and reranking run on
   services the deployment runs itself (decided 18 November 2026). The one
   decided exception is scans, in section 3.8. Which model dreaming may
   use is open (section 6, point 6).
4. **Additive.** The engine adds engrams, links and revisions. It never
   deletes a person's words and never shortens them.
5. **Revertible.** Every action the engine takes on its own is recorded
   with the state before and after, and can be undone.
6. **A person's edit wins.** An undo never overwrites something a person
   changed afterwards.
7. **One switch per mechanism, and numbers decide.** A mechanism goes on
   for everyone only after it passes its test. One that does not gain three
   points on its own test set is removed (the design of 16 November, canon
   since 17 November 2026).
8. **A person can leave with everything and be deleted fully.** The data
   download holds a person's own engrams, links, usage and activation.
   Deleting an account removes all of it. Vectors are derived and are not
   in the download. Every new kind of personal data joins both:
   associations, learned cues, the dream journal and episodes.
9. **No model call when nothing changed** (18 November 2026). Work that
   calls a model stores a hash of the exact text it sent, checks it again
   inside the job, waits a few minutes so quick edits cost one call, and
   has a daily limit per person. This holds for embedding today and for
   every later job that summarises or consolidates.
10. **Stored text is data, never instructions.** That holds for engram
    bodies, titles, file names and text read from files.

## 2. What is built

### 2.1 Storage

- **Engrams** (`engrams`) are Markdown documents in a space. Every save
  adds a full **revision** (`revisions`); revisions are never deleted,
  except when the person forgets the engram.
- **The unit of memory is the engram.** A section, the part under one
  heading, exists only as a piece that is embedded. There is no table of
  sections, blocks or facts.
- **Files** (`attachments`) are stored unchanged in object storage, with a
  description in the database.
- Each engram stores its own index line (description, size, references),
  so `load_index` does not read bodies. It falls back to the body only for
  an engram whose stored line is out of date.

### 2.2 Search

One function, `search.search_engrams`, serves `recall`, `brief` and the
connector's `search`. Its steps:

1. **Words.** Postgres full-text search in English and Dutch over stored
   search vectors. Any word may match; engrams holding every word come
   first.
2. **Meaning.** The question is embedded and compared with the vectors of
   every engram piece: one for the whole engram and one per level 2 or 3
   heading. Each piece is embedded with its title, description and heading
   path in front. An engram counts for its closest piece.
3. **Fusion.** Both rankings merge by reciprocal rank, with the meaning
   ranking counted twice (`REEF_SEARCH_SEMANTIC_WEIGHT` 2).
4. **Reranking.** A reranker reorders the first 15 results, reading two
   passages of each. It runs only for a memory of 1,000 engrams or more
   (`REEF_RERANK_MIN_ENGRAMS`). That number is a judgement, not a
   measurement: on a private set of 32 engrams the reranker added
   nothing, on 18,000 benchmark engrams it added ten points, and nothing
   was measured in between.
5. **Replaced engrams step back** (section 2.3).
6. **Files beside engrams** (decided 24 November 2026). With file reading
   on, engrams and files are candidates each on their own, and every five
   results hold at most three files (`REEF_SEARCH_FILES_IN_FIVE`). A
   file's passage follows the words of the question.

Three more rules:

- Search by meaning and reranking are optional legs. When the embedding
  service is absent, slow or down, search falls back to words.
- A question with a quoted phrase or an exclusion is searched by words
  only.
- In a question of at most three words, an engram that holds every word
  keeps its place whatever the meaning ranking says.

Decided and measured:

- The model is EmbeddingGemma 300m, on a separate internal service. It
  scored at least as well as Qwen3-Embedding-0.6B and is half the size
  (18 November 2026). The reranker is `BAAI/bge-reranker-v2-m3`.
- On the public benchmark (LongMemEval-S, 470 questions): words alone find
  the right engram in the first five for 37.5%, words and meaning for
  46.8%, and with the reranker for 56.8% (19 November 2026, through the
  product's own code).
- On the private question set, locally: words alone 80%, words and
  meaning 100%. In production with file reading on (25 November 2026):
  37 of 40 engram questions and 18 of 20 file questions.

Two findings about row-level security shape the search (17 November 2026):

- **No index for the word search.** Postgres cannot use one under row
  security, because the match operator is not marked leakproof. Search
  scans the rows a person may see. Storing the search vector removed most
  of the cost.
- **No vector index either, today.** The comparison is an exact scan. If
  one is added, it needs `hnsw.iterative_scan = strict_order`; without it
  a person who owns a small share of a large table gets no results.

Both are fine at today's size and are the first thing to revisit for a
company-sized memory.

### 2.3 Links

- Every `[[wiki link]]` becomes a row in `links` when its engram is saved.
  A link that leaves the text is marked superseded and kept.
- An engram can also say that it **replaces**, **contradicts**, is
  **about** or is **part of** another engram (26 November 2026). These are
  written in a short block at the top of a `write`.
- **Only "replaces" changes behaviour.** Search puts the newer engram
  where the old one stood. The old engram stays readable and says what
  replaced it.
- **"Replaces" needs the person's yes.** The `write` tool takes two calls:
  the first describes the effect, the second writes. A yes works once
  (RUG-97). This replaces the decision of 17 November that such a link
  waits for a click in the web interface.
- A link is visible only to a reader who can read both ends. A link from a
  shared space into a personal one is never stored.
- The table has room for more than is used: a `dream` source, validity
  dates and a confidence. Nothing writes them yet.

### 2.4 Files

- Reef reads the text inside stored files and searches it by passage
  (`REEF_FILE_TEXT`, on in production since 25 November 2026).
- PDFs with text, Word, Excel, PowerPoint, HTML, Markdown and plain text
  are read directly. A scan is read on Reef's own servers with Tesseract,
  in Dutch, French and English.
- Every file is parsed in a child process that runs as a separate user
  without rights, with an empty environment, a time limit and a memory
  limit. The first design also asked for no network; that is not built.
- Text is stored per page (`file_pages`). Passages are cut from pages when
  needed; only their vectors are stored.
- There is no derived engram for a file.
- Docling, which the first design named, is not used.

### 2.5 Use and activation

- **The usage log** (`engram_access`) holds one row each time a person, or
  an assistant for them, opens or writes an engram. Being shown in search
  results or in the index is not use and records nothing.
- **Salience.** A routine read or write counts once. A correction and an
  explicit "remember this" count three times.
- Each row carries a digest of the assistant's session.
- **Activation** (`engram_heat`) is computed per person and engram with
  the ACT-R base-level formula over the 50 most recent uses.
- **Nothing ranks or orders by activation yet.** Only the data download
  holds it. That is on purpose: thresholds need real data. Its planned
  readers are the bonus in search (section 3.2) and the budgeted index
  (section 3.1).
- **The search log** (`search_log`) can store each search and what it
  returned, private to the person, for 90 days. A search followed by
  opening an engram it did not return counts as a miss. Across people,
  only totals can be read.

Production holds very little usage data so far: 2 rows from one person
between 18 and 26 November 2026. Everything in section 3 that learns from
use depends on that changing.

### 2.6 Worker and jobs

- A worker drains a job table (`jobs`). Job rows hold ids only, never text.
- The table is under row security. One privileged function,
  `reef_claim_job`, hands the worker the next job; everything else runs as
  the person (17 November 2026).
- **No job runs across people and nothing runs nightly.** A person's own
  activity queues their work. Dreaming will be queued the same way. The
  one thing the worker does for everybody is deleting old log rows, once
  an hour, through functions that return nothing a caller can learn from.
- A model call is skipped when nothing changed: each vector stores a hash
  of the exact text that was embedded. There is a daily limit per person
  for embedding and for reading files.

### 2.7 Tools

Sixteen workflow tools since 20 November 2026 (RUG-40): `load_index`,
`recall`, `brief`, `open`, `catch_up`, `remember`, `log`, `write`,
`store_file`, `correct`, `undo`, `forget`, `share`, `instruct`, and
`search` and `fetch` for connectors.

Tools follow what a person asks an assistant to do. They are not an API
over the tables (18 November 2026). `undo` today restores an earlier
version of one engram. It has nothing to do with dreams.

### 2.8 Measuring

- **Benchmark:** LongMemEval-S loaded as ordinary engrams of a local
  person. That person never exists in production; the scripts refuse to
  run there. A slice of 24 questions runs in CI with a floor for word
  search.
  Search by meaning has no floor in CI, because CI has no embedding
  service.
- **Private set:** a person's own export and questions, kept outside git.
- **Search log:** counts misses in real use.
- **Test conversations:** scripted conversations played through a real
  assistant against a made-up account, locally and in production. They
  run with one model today. The first design asked for the same script
  under several models, to test that Reef works with any of them; that is
  not built.

## 3. What is planned

Each part names its ticket; section 4 gives the order. "Decided" items
carry their date. Everything else is the design's proposal and can still
change. Numbers such as weights and thresholds are starting values: each
sits behind its mechanism's switch and is tuned on the test sets.

### 3.1 An index with a budget (RUG-81)

Decided 25 November 2026: build this right before dreaming starts.

- The index gets a budget of 16,000 characters. Within it, the index lists
  every engram, as today.
- Past the budget it shows the `meta/` engrams, the ten main engrams (by
  inbound links, use and recency) and one line per folder.
- `load_index(space=…, folder=…)` opens one level down, within the same
  budget.
- The protocol tells the assistant that `recall` finds any engram, listed
  or not, and to `recall` a subject before filing a fact about it.

This changes a decision of 17 November, that the index must never miss an
engram. That still holds within the budget. Past it, a collapsed line
stands for several engrams.

Not in that ticket: stored groups for a large flat folder. They need
dreaming to compute and name them. Until then such a folder opens past
the budget, and the answer says so and points to `recall`.

Measured on 27 November 2026: Codex cuts a tool result at about 10,000
tokens, and a memory of today's size already reaches that.

### 3.2 Priming, spreading and a recall path (RUG-25, RUG-32, RUG-38)

- **Priming:** the last ten engrams a person used in the session count as
  extra cues for the next search, at half weight, halving with each step
  back.
- **Spreading:** relevance passes one to two hops along links, and later
  along associations. The proposal is a personalised PageRank from the
  first 20 results: ten rounds, damping 0.5.
- **Bounded.** The bonus for activation is at most 1.25 times and for
  spreading at most 1.3 times. An engram that does not match cannot reach
  the first ten on association alone.
- **Recall path:** each result says why it came back: how well it matched,
  its activation, and what spread into it from where.
- `open` returns what is associated with an engram. There is no separate
  tool for it.

Decided 17 November 2026: priming and spreading each ship behind their own
switch. They go on for everyone only after passing the mechanism gate
(section 3.4). RUG-32 builds the test set that can show whether
association finds what matching misses, and a weekly measure of churn.

### 3.3 Dream journal and undo (RUG-26)

This must exist before a dream is allowed to act.

- Every action of a dream is a journal entry with the state before and
  after.
- A whole dream can be undone, or one entry.
- **Two ways to undo** (decided 28 November 2026): in the web app's dream
  journal, and through the assistant with the `undo` tool, in two calls
  with the person's yes, as "replaces", `share` and `forget` work. This
  replaces "people only, in the web interface" of 17 November.
- An undo skips anything a person changed after the dream, and says so.
- A dream may add to an engram a person wrote only in ways that add: a
  link, a pointer to a gist. It never shortens the text. The database
  enforces this.
- `catch_up` shows what the night did.
- A dream's work can always be told apart. A revision already records
  whether an assistant, a person or the system made the change
  (`revisions.channel`), and links have a `dream` source.

### 3.4 Two gates (RUG-27)

Removing approval is safe only if something else can say no.

- **Mechanism gate.** Before a switch goes on for anyone, the mechanism
  must pass on the benchmark person. This works from the first day.
- **Nightly gate.** Each dream is checked against that person's own pairs
  of search and use. Until a person has 200 pairs, gists and duplicate
  flags are capped at ten actions per night.
- A dream **plans first**, with no transaction open, because model calls
  are slow. Then it applies its changes, replays the test questions, and
  commits or rolls back, in one short transaction.
- A drop of more than one point rolls the dream back. So does any failed
  privacy check, with an alert. Which measure the points are of, and what
  the privacy check is, are open (section 6, points 10 and 11).

### 3.5 Dreaming (RUG-28)

- A dream runs **per person, as that person**. There is no system account.
- It acts; it does not propose. There is no review queue and no approval
  tool (17 November 2026).
- **Replay** chooses what a dream works on: 60% recent, 20% surprising
  (misses, contradictions, corrections), 20% a random sample of old
  engrams. The random share keeps old structure from being overwritten.
- What a dream does: recompute activation, mark contradictions, write
  gists, flag and link duplicates, and tidy a log that grew into parts.
- **Write only what memory failed to predict.** Before a dream adds a fact
  or a link, it searches. If the search already returns it, it skips.
- **Duplicates are flagged and linked, never merged** (17 November 2026).
  Whether dreaming may later integrate them is RUG-39.
- How a dream says that one engram replaces another is open (section 6,
  point 12).
- At most 200 content actions per space per night. Updates of
  associations do not count toward it.
- **A dream runs after a person's own activity**, not every night for
  everybody (section 2.6). So whatever the design calls "nightly", such as
  decay and the three states, has to work from the time that passed, not
  from a count of nights.
- **Shared spaces:** only the owner's dream writes content into a shared
  space. Per-person state runs for every member (17 November 2026). A
  system account for spaces with an inactive owner is RUG-41.
- Private material never feeds a gist in a shared space.

### 3.6 Associations, learned cues and dormancy (RUG-29, RUG-30, RUG-31)

Each behind its own switch.

- **Associations.** Two engrams one person uses in the same session become
  associated for that person. Strength grows with a saturating step
  (`strength + 0.1 × (1 − strength)`) and decays when unused (times 0.98
  a day). Four rules keep it from running away: no credit for a hit the
  association itself produced, decay, a cap on the total strength leaving
  one engram, and pruning of associations below 0.05. This is the least
  proven mechanism. There is no seeding on the first day: recall then runs
  on written links alone.
- **Learned cues.** A search that fails, followed by opening an engram
  directly, teaches Reef that wording. A later search whose embedding is
  within cosine 0.85 of a stored cue adds that engram as a cue.
- **Dormancy.** Every engram is active, resting or dormant for each
  person: active in the top tenth by activation, dormant in the bottom
  fifth with no use in 90 days, resting otherwise. A dormant engram drops
  to the end of the index and is not a target for spreading. Search still
  finds it, and one use wakes it. Nothing is deleted. Dormancy is not
  computed until a person has 90 days of usage log. Test: fifty planted
  engrams, unused for 90 days, are still found by an exact question.

### 3.7 Episodes and gists (RUG-24)

Decided 17 November 2026. Built last.

- **Reef records each session itself** as an episode: what was read,
  written and remembered. This needs nothing from the model.
- **An episode holds no search questions** (decided 28 November 2026).
  Those stay in the search log and go after 90 days, as the privacy page
  promises. The decision of 17 November listed "what was searched" as part
  of an episode; that part is dropped.
- Episodes are kept forever and go dormant over time, like engrams. Replay
  keeps sampling old ones.
- Lines written with `remember` attach to their episode and are kept after
  they are compiled into engrams.
- The protocol asks assistants for a short **wrap-up** at natural stopping
  points: what was discussed, what was decided and why, what is open.
  Wrap-ups go to the personal space by default. They are a setting per
  person, on by default. The wrap-up instruction is measured under several
  models first: weak models must not get worse at their other tasks.
- Dreaming compiles those lines into engrams, with a link back to the
  episode. So "why did we decide this" has an answer.
- Only the person sees their episodes. Nobody can edit one. The person can
  delete one, or a line in it.
- No transcripts, and no separate kind of engram for episodes.
- **Gists** are allowed, behind a switch and the gate. A gist is an engram
  Reef wrote. It is marked as such and links to every source with a
  `derived_from` link. A source is never ranked below its gist.

Not built today: there is no table for episodes or sessions. The usage
log, the search log and the call log each carry a digest of the session;
revisions do not. On 17 November the session record was assigned to
RUG-23, which closed without it. It is part of this work now.

### 3.8 An outside reader for scans (RUG-37, phase 2)

Decided 20 and 21 November 2026.

- Every scan is read locally first, whatever the switches say.
- A scan may also go to an outside model, where **both** the deployment
  and the space allow it. The space's switch is off by default and its
  owner sets it.
- The provider is the Gemini API on the paid tier. Never the free tier,
  which trains on the data.
- Only scans ever leave Reef. Text PDFs, vectors and search stay local.

Nothing of this is built.

## 4. Build order

| Order | What | Ticket | State |
|---|---|---|---|
| 1 | Worker, jobs, vectors in Postgres, stored index lines | RUG-33 | Done |
| 1 | Usage log and activation | RUG-23 | Done |
| 2 | Benchmark, private set, search log | RUG-34 | Done |
| 3 | Search by meaning, reranker | RUG-35 | Done |
| 4 | Links, typed links, replaced engrams | RUG-36 | Done |
| 5 | Reading files | RUG-37 | Phase 1 done, phase 2 open |
| 6 | Priming, spreading, recall path; its test set | RUG-25, RUG-32 | Next in this order |
| 7 | Dream journal and undo; the two gates | RUG-26, RUG-27 | |
| 8 | Index with a budget | RUG-81 | Right before dreaming |
| 9 | Dreaming | RUG-28 | |
| 10 | Associations, learned cues, dormancy | RUG-29, RUG-30, RUG-31 | |
| 11 | Episodes and gists | RUG-24 | |

The plan of 17 November put step 6 before step 5. Reading files was built
first.

Decisions that run beside the build: RUG-22 (section 6), RUG-38 (switch
priming and spreading on for everyone), RUG-39 (duplicates), RUG-41 (a
system account).

All changes to the database are additive. With every switch off, nothing
behaves differently.

## 5. What was decided against, and why

- **A graph of extracted entities, or a graph database.** Engrams stay the
  nodes. Postgres covers one to three hops at Reef's size.
- **A table of facts** ("assertions"), each with its own validity.
  Deferred, not rejected: the link table keeps room for it.
- **A machine-written digest in place of the index.** The index is a list.
- **Summaries that replace their sources.** The one controlled study found
  that harmful. Gists add; they never replace.
- **A review queue.** Dreaming acts within the additive rule, and the
  gates and undo carry the safety.
- **A hosted API for embeddings.** It would send engram text to a third
  party.
- **Spreading for everyone by default.** The evidence for it is weak, so
  it has to earn its place on the test set.
- **An index on the search columns.** Section 2.2 says why.

## 6. Open

From the design (RUG-22):

1. Should the bonuses for use and association stay bounded, or may they
   dominate? Decide from the test set of RUG-32.
2. May dreaming add links to engrams a person wrote, or only write its own
   engrams? Section 3.3 holds the current proposal: additions only.
3. Should a space share part of what Reef learns from use? That reopens
   the leak of reading behaviour.
4. Should a step whose actions are often undone by hand lower its own cap,
   and at what rate?
5. What per-person associations cost at scale. Pruning bounds them; the
   bound is untested.

Found while merging the documents:

6. **Which model dreams.** On 15 November it was decided that dreaming
   calls an outside model (an Anthropic Haiku-class model, its key in the
   worker only). On 18 November a hosted API for embeddings was ruled out,
   because it sends engram text to a third party. Dreaming with an outside
   model sends engram text out too. The choice is made in the design of
   RUG-28: an outside model behind switches (as for scans), a model Reef
   runs itself, or both.
7. **Human writes.** The design gives a person's own write in the web
   editor twice the weight of an assistant's. Today it counts once
   (RUG-96).
8. **The index within the budget.** The plan of 17 November orders the index by
   state: active first, dormant last. Today it is ordered by path. No
   later decision changed the plan, so it stands until RUG-81's design
   settles it. RUG-81 also asks whether use helps choose the main
   engrams, whether `remember` should check on the server that an engram
   about the subject exists, and whether Codex's smaller limit moves the
   ticket forward.
9. **Validity dates.** The first design gave engrams and links a period
   in which they hold, and let a search for a period prefer what was
   valid then. The link table has the columns; nothing writes them.
   Decided 26 November 2026: they wait for dreaming.
10. **What the gates measure.** The design says nDCG@10: a drop of one
    point rolls a dream back, and a mechanism must gain three. The
    benchmark reports "found in the first five" and the mean reciprocal
    rank, not nDCG. RUG-27 has to choose.
11. **The privacy check in the gate.** The first design had a nightly leak
    test: a second, made-up person with a separate space replays the
    day's questions and must get nothing back. It needs something that
    runs across people, which Reef does not have. RUG-27 has to say what
    the check is.
12. **A dream's "replaces".** An assistant's "replaces" needs the person's
    yes (26 November 2026). The typed links design left the dreamer out.
    A dream acts without asking, so either it never writes "replaces" and
    only flags, or the yes is asked the next morning. RUG-28 or RUG-39
    decides.
13. **A daily limit on what counts as use.** The plan of 17 November keeps
    one per person, so a loop cannot pump an engram's activation. It is
    not built, and nothing dropped it. It matters once something ranks by
    activation.
14. **A guard against shrinking.** The first design refuses a `write` that
    makes an engram more than half shorter, unless a reason is given. It
    is not built, and nothing dropped it.
15. **A search by meaning that can come back empty.** Today the closest
    engrams always come back, however far they are. A distance limit was
    left open in RUG-35.
16. **Duplicates** (RUG-39): merging rewrites a person's words, which
    breaks the additive rule. Options are a merged engram Reef writes that
    links to both originals, or a merge kept only if the gate and the
    person's undo rate support it.
17. **A system account** (RUG-41): a shared space whose owner is inactive
    gets no dreaming. An account that is an ordinary member of each shared
    space would fix that. It must never read personal spaces, members
    must be able to see it, and seats, authorship and deletion each need
    a rule for it.
18. **On for everyone?** (RUG-38): a yes or no for priming and for
    spreading, from numbers.

## 7. Risks being watched

- **Too little use to learn from.** Activation, the nightly gate and
  dormancy all need usage data. Production has very little.
- **Associations running away.** The four stability rules exist for this,
  and the mechanism is the first to be removed if it does not help.
- **A night that makes things worse.** That is what the gates and undo are
  for. Until a person has 200 pairs, the nightly gate can judge little.
- **Size.** Search scans the rows a person may see. That is a question for
  a company-sized memory.

## 8. Decisions, by date

| Date | Decision |
|---|---|
| 15 Nov 2026 | Embedding and reranking run on Reef's own machines. |
| 15 Nov 2026 | A benchmark person is the first gate; a private set is the second. |
| 17 Nov 2026 | The self-organising design is canon; the first design's groundwork is carried over. |
| 17 Nov 2026 | The unit is the engram. No table of blocks or facts, no digest, no system account. |
| 17 Nov 2026 | Priming and spreading ship behind a switch. |
| 17 Nov 2026 | Gists are allowed, behind a switch and the gate. Sources never rank below their gist. |
| 17 Nov 2026 | Episodes: recorded by Reef, private, with wrap-ups (section 3.7). |
| 17 Nov 2026 | Duplicates are flagged and linked, never merged. |
| 17 Nov 2026 | Only the owner's dream writes content into a shared space. |
| 17 Nov 2026 | The job table is under row security, with one function for claiming. |
| 17 Nov 2026 | No index on the search columns under row security. |
| 18 Nov 2026 | Cove becomes space, page becomes engram, everywhere. |
| 18 Nov 2026 | Tools follow workflows, not tables. |
| 18 Nov 2026 | Search by meaning ships only if it beats word search by 10 points on the benchmark. |
| 18 Nov 2026 | A separate internal embedding service. Each piece is embedded with its title, description and heading path. |
| 18 Nov 2026 | Searches are logged for everyone, 90 days, private to the person; only totals across people. |
| 18 Nov 2026 | No model call when nothing changed: hash, recheck in the job, a short wait, a daily limit. |
| 20 Nov 2026 | Sixteen workflow tools replace the 31 (RUG-40). |
| 20 Nov 2026 | Only scans may leave Reef, and only where deployment and space both allow it. |
| 21 Nov 2026 | Every scan is read locally first. The outside reader is the paid Gemini API. |
| 24 Nov 2026 | Files and engrams are candidates each on their own; at most three files in five results. |
| 25 Nov 2026 | The index with a budget is built right before dreaming. |
| 26 Nov 2026 | An assistant's "replaces" needs two calls and the person's yes. This replaces "confirm in the web interface" of 17 Nov. |
| 26 Nov 2026 | Validity dates and confidence on links wait for dreaming. |
| 27 Nov 2026 | A yes for "replaces" works once (RUG-97). |
| 28 Nov 2026 | An episode holds no search questions; those go after 90 days with the search log. |
| 28 Nov 2026 | A dream can be undone in the web app and through the assistant, with the person's yes. |
| 28 Nov 2026 | Which model dreams is open until the design of RUG-28. |

## 9. Where this came from

This document replaces three earlier designs. None of them was ever in
git, and nothing in them is needed to build from this one.

- **The first design** (15 November 2026): search by meaning, links,
  reading files, and a dreamer that proposes changes for approval.
- **The self-organising design** (16 November 2026): activation,
  associations, and dreaming that acts without asking.
- **The plan of 17 November 2026**, which fitted those two to Reef as it
  was and recorded that day's decisions.

Where the text above says "the first design", "the design" or "the plan
of 17 November", it means these.

Designs of single parts, which stay valid: `docs/specs/2026-11-19-workflow-tool-set-design.md`,
`docs/specs/2026-11-21-file-text-design.md`,
`docs/specs/2026-11-24-files-beside-engrams-design.md`,
`docs/specs/2026-11-26-typed-links-design.md`,
`docs/research/2026-11-20-fast-index-spike.md`,
`docs/research/2026-11-21-scan-readers.md`.

`docs/spec.md` is the original design of August 2026. Its positions "no
vector database" and "no embeddings" are replaced by section 2.2, and its
words "page" and "cove" by the glossary.
