# Retrieval benchmark

This measures how often search finds the page that holds the answer. No
change to search should ship without running it.

## Run it

```bash
just eval-download     # once: 277 MB, checked against a known hash
just eval-seed         # loads 100 questions' sessions, about 4,500 pages
just eval              # asks the questions in every search mode
```

`just eval-seed all` and `just eval all` use all 470 answerable questions.
That is about 19,000 pages.

**Local only.** The scripts stop if the database is not on this machine, or
if any `RAILWAY_` variable is set, or if anybody has signed in to that
database. The benchmark must never reach
production: it creates a person nobody invited and thousands of pages of
other people's chats.

## What it measures

The data is LongMemEval-S. Each question comes with the chat sessions that
hold its answer and about forty that do not. Every session becomes a page of
one dedicated person, `eval@reef.local`, written through the same code an
assistant uses.

For each question the harness searches and looks at the pages returned.

| Column | Meaning |
|---|---|
| `any@5` | A page with the answer is in the first five results |
| `all@5` | Every page needed for the answer is in the first five |
| `MRR` | How high the first right page ranks. 1.0 means always first |

It does not ask a model to answer. It checks retrieval only.

## Two differences from the published benchmark

- **All sessions are loaded into one memory.** The benchmark gives each
  question its own small haystack. Reef has one memory per person, so the
  sessions of all chosen questions are searched together. That is harder.
  Do not compare these numbers with published ones. Compare them with each
  other.
- **Session dates are not stored.** The benchmark gives the same session
  different dates under different questions. The date is in the page title
  only.

## Search modes

| Mode | What it does |
|---|---|
| `lexical` | Search as it ships: the question as typed |
| `lexical-any` | The same search, with the question rewritten so its words are alternatives |
| `hybrid` | Words and meaning together, as search ships once a reef has an embedding service |

`hybrid` needs a local embedding service and the eval person's vectors:

```bash
ollama pull embeddinggemma:300m
export REEF_EMBEDDING_URL=http://127.0.0.1:11434
just worker            # embeds the pages; lift REEF_EMBEDDING_DAILY_CAP for the benchmark
just eval-private
```

Without them `hybrid` scores the same as `lexical`, because that is how
search behaves without a service. The run stops if the address is not on
this machine.

New modes are added to `MODES` in `evalkit.py`.

## Results

100 questions, 4,546 pages, first five results.

| Date | Mode | any@5 | all@5 | MRR | What changed |
|---|---|---|---|---|---|
| 18 Nov 2026 | `lexical` | 24.0% | 10.0% | 0.195 | First measurement. A page had to hold every word |
| 18 Nov 2026 | `lexical` | 53.0% | 24.0% | 0.437 | Any word may match, full matches first, rank scaled by page length |

**53.0% is the line that embeddings have to beat.**
The report on what moved the score from 24.0% to 53.0% is in
[`docs/eval/2026-11-18-keyword-search.md`](../../docs/eval/2026-11-18-keyword-search.md).

By question type, `any@5`, for the current search:

| Type | Questions | `lexical` |
|---|---|---|
| knowledge-update | 15 | 73.3% |
| multi-session | 26 | 50.0% |
| single-session-assistant | 12 | 75.0% |
| single-session-preference | 6 | 0.0% |
| single-session-user | 14 | 42.9% |
| temporal-reasoning | 27 | 51.8% |

Preference questions share no words with the page that answers them. No
keyword search finds them.

All 470 questions, 18,239 pages, first five results, through the product
code with EmbeddingGemma 300m on a local Ollama.

| Date | Mode | any@5 | all@5 | MRR | Per search |
|---|---|---|---|---|---|
| 19 Nov 2026 | `lexical` | 37.5% | 18.7% | 0.262 | 0.25 s |
| 19 Nov 2026 | `hybrid` | 46.8% | 25.1% | 0.333 | 0.60 s |

Add a row to the table whenever a change to search moves the numbers.

## The slice in the test suite

`tests/fixtures/longmemeval_slice.json` holds 24 questions with their
evidence and two other sessions each. `tests/test_eval_harness.py` loads it
and fails when a search mode scores below its floor. The slice is small and
easy on purpose. It catches a regression. It does not measure quality.

Rebuild it with `python scripts/eval/seed.py --write-slice tests/fixtures/longmemeval_slice.json`.

## A test account

`just test-account` creates `test@reef.local` in the local database and
loads the slice into it: 80 made-up conversations, nothing personal, no
download. Open it with `just dev test@reef.local`.

The questions and their expected pages are in
`tests/fixtures/longmemeval_slice.json`, so you know what a search should
find.

## The private set

Questions about your own pages. It tests what the benchmark cannot: Dutch,
real page style, and the questions you actually ask.

`scripts/eval/private/` is ignored by git. Your pages and the questions
about them are personal data and must not be committed.

1. On the site, export your pages as JSON. Save the file as
   `scripts/eval/private/export.json`.
2. Load it: `just eval-private-seed`. The pages go to a second local person,
   `private-eval@reef.local`, apart from the benchmark.
3. Write `scripts/eval/private/questions.json`: a list of entries like
   `{"id": "1", "kind": "dutch", "text": "...", "expected": ["personal/notes/x.md"]}`.
   A path is the cove's alias, a slash, and the page's path.
4. Run it: `just eval-private`.

The export holds only what you can see yourself. Nothing is read from the
production database, and nobody else's private pages come along. To load a
newer export, recreate the local database first.

## Source and licence

LongMemEval, Wu et al., 2024, cleaned release, MIT licence.
<https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned>
