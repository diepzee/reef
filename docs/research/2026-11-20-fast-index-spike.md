# A fast `load_index`: what it costs today, and what to show instead (20 November 2026)

Time-boxed spike on branch `spike/fast-index`. The code is throwaway and lives in
[`spike/fast_index/`](../../spike/fast_index/). The raw aggregates are in
[`spike/fast_index/results/`](../../spike/fast_index/results/).
This spike replaces the context-ceiling measurement of runbook Phase 7.

## The answer

- **The index is too big already, and on the owner's memory the cause is files, not engrams.**
  The private memory (32 engrams, 445 files) gives a 215,600-character payload, about 54,000 tokens.
  File lines are 87% of it.
- **Claude Code cuts it.** Claude Code 2.1.329 replaces any tool result over about 50,000 characters
  with a 2 KB preview and a file path. Today's index crosses that line at about 90 engrams, or about
  90 files. The operating protocol is the last field of the payload, so the cut removes it.
- **The best variant is "V4, budgeted".** List every engram as one short line while the index fits in
  16,000 characters. Past that, list the main engrams and collapse the rest into folder and group lines.
  - Private memory: 17,700 characters with the protocol (4,900 without), every engram still listed.
    That is 92% smaller than today, with nothing hidden.
  - 536 to 4,947 engrams: about 28,500 characters, whatever the size.
- **With Claude in the loop, the index decides one thing: where a new fact goes.**
  Lookups, orientation and briefs succeeded in all 24 runs, with every variant, because the assistant
  answered through `recall` and `brief`. When the engram a fact belonged to was hidden behind a collapsed
  line, the assistant staged the fact in the inbox instead (0 of 4 filed). One protocol rule fixed that
  (2 of 2 filed).
- **Recommendation.** Ship the format fixes now (files as counts, one line per engram, protocol first, a
  16,000-character budget). Collapse past the budget with folders, the top engrams by links and use, and
  stored groups that dreaming labels. Add the rule "the index is not complete: `recall` the subject before
  you `remember` or `write`".

Tokens are estimated as characters divided by 4, except in the LLM runs, where they are counted.

## 1. What `load_index` costs today

| Memory | Engrams | Files | Payload (characters) | Tokens (est.) | Median | p95 |
|---|---:|---:|---:|---:|---:|---:|
| Private | 32 | 445 | 215,600 | 53,900 | 12 ms | 13 ms |
| Benchmark | 104 | 0 | 51,800 | 12,900 | 9 ms | 10 ms |
| Benchmark | 524 | 0 | 213,000 | 53,300 | 19 ms | 22 ms |
| Benchmark | 2,046 | 0 | 795,100 | 198,800 | 60 ms | 87 ms |
| Benchmark | 4,947 | 0 | 1,905,400 | 476,300 | 142 ms | 172 ms |

- **Latency is not the problem.** The method takes 9 to 142 ms, warm, over 20 calls, serialisation
  included. It grows linearly with the number of engrams.
- **Size is the problem.** Each engram adds about 380 characters (benchmark) to 480 characters (private,
  where engrams link to each other). Each file adds about 420 characters. The protocol and persona add
  a fixed 12,000 to 12,500 characters.
- **Logs make it worse fast.** A log of 100 small engrams a year adds about 40,000 characters a year.
  A company memory of 5,000 engrams is 476,000 tokens: more than any context window.

### Which fields dominate

On the private memory, files are 87% of the payload:

| Part | Share of the payload |
|---|---:|
| File descriptions | 43% |
| File names | 14% |
| File keys | 14% |
| Operating protocol and persona | 6% |
| File MIME types | 6% |
| File's engram path | 5% |
| File sizes | 3% |
| Engram references (outgoing links) | 3% |

Per engram on the private memory, the outgoing `references` list is the largest field (182 characters),
ahead of the description (106).

On the benchmark, the engram fields dominate: description 43%, path 12%, update time 10%, title 10%,
last editor 5%, tags 5%, references 4%, size, version and inbound count about 3% each. Benchmark
descriptions are long (166 characters on average) because each is a user's first chat message.

### What a client does with it

I probed the size at which Claude Code 2.1.329 stops passing a tool result to the model:

| Payload | What the model received |
|---:|---|
| 28,600 characters | All of it |
| 44,500 characters | All of it |
| 56.4 KB, as Claude Code reports it | A 2 KB preview and a file path |
| 121.9 KB | A 2 KB preview and a file path |
| 212.3 KB | A 2 KB preview and a file path |

The limit is between 44,500 characters and 56.4 KB, probably 50,000 characters. With `--tools ""` the model
cannot read the saved file, so it works without the index. The preview starts with the first engrams,
so the protocol, which comes last, is lost. The owner's own memory is past this limit today.
`MAX_MCP_OUTPUT_TOKENS=200000` did not change it. What the Claude app and the mobile app do was not
measured.

## 2. Which signal picks the engrams that matter

### Variants

| Variant | What the index shows |
|---|---|
| today | Every engram and every file, with all fields |
| slim | Every engram as one line: `path \| title \| description #tags [n files]`. Title left out when it repeats the file name. Files become a count |
| V1 | Engrams at the space's root, and one line per top-level folder: name, count, latest date |
| V2 | V1 plus the 10 engrams with the highest score from inbound links, use and recency |
| V3 | One line per semantic group (k-means on the stored whole-engram vectors, k = √n), labelled by its most central engram, with a count |
| V4 | **The proposal.** The slim list if it fits a budget (8,000 or 16,000 characters). Otherwise: `meta/`, the top 10 by score, a line per folder naming its 3 top titles, and groups inside any container too large for one line, as many as the budget holds |

The score is `ln(1 + inbound links) + activation + 0.5 × recency + 1 for a core tag`.

### How coverage was judged

The brief asked how many expected engrams are visible, or one expansion away. The second half is 100%
for every variant that puts each engram under some line, so it says nothing. I added three measures:

- **Routed at 3:** rank the index lines by how close their text is to the question (local embeddings),
  and check that the first 3 lines hold an expected engram, directly or inside a collapsed line.
- **Two-step found:** look at those 3 lines, expand the collapsed ones, then open the 3 engrams closest to
  the question. Found when one of them is expected. Every variant opens 3 engrams, so this is fair.
- **Characters expanded:** what those expansions cost, the median per question.

The judge is an embedding model, not an assistant. It is a proxy for orientation. Answering does not need
the index: `recall` finds every private-set answer in its first five results. So these numbers measure
how well the index orients an assistant, not whether it can answer. I weigh them low against size and
against the LLM runs.

### Private memory: 32 engrams, 40 questions, 50 expected engrams

| Variant | Payload | Index only | Lines | Visible | Routed at 3 | Two-step found |
|---|---:|---:|---:|---:|---:|---:|
| today | 215,600 | 202,800 | 32 | 100% | 82% | 82% |
| slim | 17,700 | 4,900 | 32 | 100% | 82% | 82% |
| V1 | 16,800 | 4,000 | 27 | 86% | 82% | 82% |
| V2 | 18,000 | 5,200 | 34 | 100% | 82% | 82% |
| V3 | 14,100 | 1,300 | 7 | 0% | 62% | 52% |
| V4, 8k or 16k | 17,700 | 4,900 | 32 | 100% | 82% | 82% |

- The private memory fits any budget once files are counted, so V4 is the slim list.
- 31 of its 32 engrams answer some question. It cannot test which engrams are "main"; it can test what
  collapsing costs. Folders hide 7 of the 50 expected engrams (V1). Groups alone lose 30 points of
  orientation (V3).
- Its structure: 1 space, 3 folders holding 8 engrams, 24 engrams at the root. 26 engrams have inbound
  links. 30 are tagged, with 14 distinct tags. Folders carry little hierarchy here; links carry more.

### Benchmark: 524, 2,046 and 4,947 engrams

Two-step found, with the median characters expanded per question:

| Variant | 524 engrams | 2,046 engrams | 4,947 engrams | Payload at 4,947 |
|---|---|---|---|---:|
| today | 55% | 43% | 38% | 1,905,400 |
| slim | 55% | 43% | 38% | 1,056,200 |
| V1 | 55%, +110,300 | 43%, +431,500 | 38%, +1,043,800 | 12,700 |
| V2 | 9% | 7% | 6% | 14,800 |
| V3 | 36%, +17,800 | 39%, +26,400 | 26%, +57,000 | 20,700 |
| V4, 8k | 46%, +11,400 | 25%, +34,000 | 20%, +95,600 | 20,000 |
| V4, 16k | 46%, +6,100 | 32%, +16,200 | 26%, +49,200 | 28,400 |

- Benchmark engrams are flat (`sessions/…`), with no links, no use and no meaningful titles. They test
  scale and grouping, not folders or main concepts. No score can tell an evidence session from the 40
  around it, so "visible" is 0% for every collapsed variant.
- V1 is useless on a flat memory: its one folder line expands to the whole list.
- V2 is worse than useless here: 10 arbitrary "top" engrams crowd out the folder line.
- Groups (V3, V4 at 16k) keep about two-thirds of the full list's orientation (26% against 38%) for
  1.5% of its size at 4,947 engrams.
- **One level of groups is not enough at 5,000.** The median V4 expansion is 49,200 characters, at
  the client's limit. Expanding a group must itself respect the budget, which needs a second level.
- At 104 engrams there are only 2 questions; I report that memory for size only.

### Cost of the signals

| Engrams | Read the vectors | V3 groups | V4, 16k |
|---:|---:|---:|---:|
| 524 | 57 ms | 31 ms | 63 ms |
| 2,046 | 245 ms | 391 ms | 489 ms |
| 4,947 | 501 ms | 1,089 ms | 1,183 ms |

Grouping on every call would make `load_index` 10 times slower. The groups also change between builds,
so a reference like `sessions/~12` would point elsewhere next time. Groups have to be computed in the
background and stored.

### Signals I could not measure

- **Use (activation).** The export carries no usage, and seeding records none. Every local memory had
  zero activation. The score includes it, but no number here shows what it adds.
- **Recency** on the benchmark comes from the session date in the title. Seeding wrote every engram at
  the same moment.

## 3. Effect on an assistant

### Setup

- Claude Code 2.1.329, `claude -p`, model Sonnet 5.5 (`--model sonnet`), only the Reef tools
  (`--strict-mcp-config`, `--tools ""`, `--allowedTools "mcp__reef__*"`).
- A local stdio Reef on a fresh copy of the test memory for every run, with local embeddings.
- **Test memory, 536 engrams:** the 523 benchmark sessions of 11 questions, plus 12 made-up engrams in
  folders (`profile.md`, `people/`, `projects/`, `home/`, `health/`, `work/`) that link to each other.
  No private data.
- **Variants:** today; V2; V4 at 16,000 characters. For placement also V4 with no top engrams, so the
  target engram is hidden, with and without a stronger protocol rule.
- The fast variants replace the protocol's index paragraph: "The index is a map, not a search … Before
  you write about something new, expand the line where it would belong or `recall` the topic." The
  stronger rule adds: "The index is not complete … Before `remember`, `write` or `log` about a subject,
  call `recall` on the subject first."
- Each task ran twice per variant. The repeats gave the same outcome every time, and the same calls in
  16 of 17 pairs.

| Task | Prompt | Checked by |
|---|---|---|
| Orientation | "What do you have on my Japan trip?" | 4 of 6 trip facts in the answer |
| Lookup, curated | "When does my car insurance renew?" | "14 March" |
| Lookup, detail | "How many skeins of worsted weight yarn did I find in my stash?" | "17" (a benchmark answer) |
| Placement | "Remember this for me: Ben and Sofie had a baby girl last Sunday. She's called Lotte." | The database: does `people/ben.md` now hold it? |
| Brief | "I see the physio tomorrow. Brief me on where I stand with my running and my knee." | IT band, 17 May half marathon, latest 15 km run |

### Results

| Variant | Index the model received | Succeeded | Tool calls | Input tokens, all 10 runs | Wall time |
|---|---:|---:|---:|---:|---:|
| today | 2,400 characters of 212 KB (cut) | 10 of 10 | 22 | 367,700 | 98 s |
| V2 | 14,800 | 8 of 10 | 21 | 393,300 | 100 s |
| V4, 16k | 28,600 | 10 of 10 | 24 | 511,500 | 107 s |

Placement only:

| Variant | Is Ben's engram in the index? | Where the fact went | Calls |
|---|---|---|---|
| today (index cut away) | No index | Ben's engram, 2 of 2 | `load_index`, `recall`, `correct` |
| V2 | Behind `people/ (4 engrams)` | The inbox, 2 of 2 | `load_index`, `remember` |
| V4, 16k | Listed | Ben's engram, 2 of 2 | `load_index`, `open`, `correct` |
| V4, no top engrams | Behind `people/ (4 engrams): Ana; Mira; Noor` | The inbox, 2 of 2 | `load_index`, `remember` |
| Same, with the stronger rule | Same | Ben's engram, 2 of 2 | `load_index`, `recall`, `correct` |

What this shows:

- **Answering does not depend on the index.** Every lookup, orientation and brief went through `recall`
  or `brief`, with any variant, also when the index was cut to a preview. The server's own instructions
  were enough to send the assistant there.
- **A partial index that looks complete misleads placement.** When the assistant did not see the engram a
  fact belonged to, it concluded there was none and staged the fact in the inbox. The protocol allows
  that: a tidy-up files inbox entries later. But Ben's engram stays out of date until then. With no index
  at all, the assistant searched first and filed correctly.
- **One protocol rule fixes it.** Saying that the index is incomplete, and that `recall` comes before
  `remember`, made the assistant search and file correctly in both runs.
- **No assistant expanded a collapsed line** in 34 runs. Expansion is a convenience for browsing, not the
  route assistants take. `recall` is.
- **Token cost.** In Claude Code at this size, today's index costs nothing because it never arrives. The
  V4 index costs about 9,000 more input tokens per short conversation (36,100 against 26,900 for a
  lookup). A client that delivered today's index whole would pay about 53,000 tokens for it, and again
  on every later turn as cached input.

## 4. Recommendation

### What to build

1. **Now, whatever else is decided:**
   - Files out of the index. Show a count on their engram's line and per space. List them when their
     folder is expanded. `recall` already finds files by name and description.
   - One line per engram: `path | title | description #tags`. Drop version, size, last editor, the
     references list, the inbound count and the full timestamp. These belong to `open`.
   - Put the operating protocol first in the payload, so a client that cuts keeps it.
   - On the private memory this alone takes the payload from 215,600 to 17,700 characters, with every
     engram still listed.
2. **A budget: 16,000 characters for the index**, about 4,000 tokens, and about 30,000 for the whole
   payload with protocol and persona. That leaves room under the 50,000 at which Claude Code cuts.
   It lists about 100 engrams of the private memory's style in full. 8,000 is too tight past 2,000
   engrams: expansions double in size and orientation drops (25% against 32%).
3. **Past the budget, V4:** `meta/`, the top 10 by inbound links, use and recency, a line per folder
   with its count, latest date and 3 top titles, and stored groups inside any container too large for
   one line.
4. **Expansion: `load_index(space=…, folder=…)`**, where `folder` may also name a group
   (`sessions/~12`). It returns the same budgeted view one level down: the members as lines if they fit,
   otherwise sub-folders and sub-groups. No answer is ever larger than the budget. At 5,000 flat engrams
   this needs two levels of groups.
5. **The protocol must say:**
   - "The index lists the main engrams, not all of them. A collapsed line stands for several."
   - "Answer with `recall`. It finds any engram, listed or not."
   - "The index is not complete. Before `remember`, `write` or `log` about a subject, `recall` the
     subject first. If an engram covers it, add the fact there."

### Derived or explicit hierarchy

Derived is enough to choose the main engrams. Grouping large flat containers needs stored structure.

- **Choosing works from signals.** Links mark the main engrams of a curated memory: 26 of 32 private
  engrams have inbound links. Use should help too, but was not measurable here.
- **Grouping on the fly does not work.** It takes up to 1.2 seconds, changes the references between
  calls, and labels each group with one engram's first line. Those labels orient poorly: 62% against
  82% for the full list on the private memory.
- **So store the groups.** Let dreaming compute them in the background, keep their references stable,
  and write each a one-line label. These are the "map" engrams of the brief, needed only where a folder
  or space is too big for one line. People should not have to maintain a hierarchy by hand.

### Risks

- **Hidden engrams lead to misplaced writes.** Measured above. The protocol rule is the guard; a
  server-side check in `remember` ("an engram about this exists") would be a stronger one.
- **Clients differ.** Claude Code cuts at about 50,000 characters. The Claude app and the mobile app
  were not measured. Phase 7 should measure the new payload on the phone.
- **The protocol is lost today** whenever a client cuts the index, because it comes last.
- **The main-concept score is barely tested.** Use was zero everywhere, the benchmark has no links, and
  the private memory is too small to need selection.
- **Stored groups are new state** to keep current as engrams change.
- **The evaluation is small.** One private memory of 32 engrams, a flat benchmark, an embedding model as
  judge, and one model in one client on one test memory.

## What I cut or could not measure

- **Use as a signal:** no usage data in any local memory.
- **The Claude app and the mobile app:** only Claude Code was run.
- **Today's index delivered whole to a model:** Claude Code cuts it at every size worth testing. Its
  cost is estimated, not measured.
- **LLM runs:** one test memory (536 engrams), one model, each task twice. Placement got 4 extra runs.
- **A real tokenizer:** sizes use characters / 4.
- **Recursive expansion, other values of k, grouping by tag:** not prototyped.
- **Section vectors at 2,046 and 4,947 engrams:** the worker embedded the whole-engram piece only (see
  below). Search was not used on those memories.

## Where I departed from the brief

- **The private memory was not loaded with `scripts/eval/seed.py --private`.** That script puts every
  space's alias in front of each path. That is right for search, but for the index it drops the tags,
  breaks every same-space wiki link, leaves out the 445 files, and turns the persona into an ordinary
  engram. Its index is 22,800 characters, against 215,600 for the faithful load. I loaded the same
  export with its original paths, tags, persona, dates and file descriptions
  (`spike/fast_index/seed_private.py`). The export has one space, so no paths collide.
- **Vectors at 2,046 and 4,947 engrams** were made by the worker with
  `REEF_EMBEDDING_PIECE_MAX_CHARS=1000000`. That gives each engram its whole-engram vector only, which
  is identical to the one production stores. Full sections would have taken about an hour more.
- **"One expansion away"** is replaced by the routing measures above, for the reason given there.

## How to rerun

```bash
spike/fast_index/makedb.sh reef_spike_b2000
REEF_DATABASE_URL=postgresql://reef:reef@localhost:5433/reef_spike_b2000 \
  REEF_EMBEDDING_URL=http://127.0.0.1:11434 REEF_EMBEDDING_DELAY_SECONDS=0 \
  REEF_EMBEDDING_DAILY_CAP=1000000 .venv/bin/python scripts/eval/seed.py --questions 44
spike/fast_index/embed_all.sh <log dir> reef_spike_b2000
REEF_DATABASE_URL=postgresql://reef:reef@localhost:5433/reef_spike_b2000 \
  REEF_EMBEDDING_URL=http://127.0.0.1:11434 \
  .venv/bin/python spike/fast_index/measure.py bench 44 --out <dir>/b2000.json
.venv/bin/python spike/fast_index/tables.py spike/fast_index/results \
  spike/fast_index/results/llm_runs.jsonl
```

Question counts per memory: 2 → 104 engrams, 11 → 524, 44 → 2,046, 110 → 4,947.
The private memory: `seed_private.py`, then `measure.py private`. It prints and stores aggregates only.

The LLM runs: build `reef_spike_llm_tpl` as a copy of `reef_spike_b500`, add
`spike/fast_index/llm/testlayer.py`, embed it, then run `spike/fast_index/llm/run.py`. The spike-only
`expand` argument on `load_index` and `REEF_SPIKE_INDEX` in the server's environment serve the variants
(`src/reef/methods/find.py`, `spike/fast_index/hook.py`).

Databases kept for a rerun: `reef_spike_b500`, `reef_spike_b2000`, `reef_spike_b5000` (with vectors;
embedding them took about 30 minutes) and `reef_spike_llm_tpl` (the LLM test memory). The private memory's database is
dropped.
