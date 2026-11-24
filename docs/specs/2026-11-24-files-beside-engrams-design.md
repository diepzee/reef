# Files beside engrams in search: design

Date: 24 November 2026. Status: approved by Wouter in conversation. Ticket: RUG-77, found by RUG-60.

## Why

With file reading on (`REEF_FILE_TEXT`), search finds files by their text. On
Wouter's account (638 files, 2,804 pages) that worked: 18 of 20 file
questions found a right file in the first five results, against 0 of 20
before. Two things went wrong at the same time, and RUG-60 may not switch
reading on for everyone until they are fixed.

| Measured on 60 private questions | Reading off | Reading on | RUG-60 needs |
|---|---|---|---|
| Engram questions with a right engram in the first five | 38 of 40 | 29 of 40 | at least 38 |
| File questions with a right file in the first five | 0 of 20 | 18 of 20 | at least 16 |
| File questions whose passage holds the answer | 0 of 20 | 11 of 20 | at least 12 |
| Median time of a search | 90 to 100 ms | 135 to 146 ms | at most 100 ms more |

1. **Files crowd out engrams.** Both rankings, by words and by meaning, take
   their first 50 rows from engrams and files together. Files held 45 of the
   50 places by meaning and 41 by words. The fusion scores a row by its
   place, so an engram sinks for every file in front of it. All 40 right
   engrams were still in the first fifty results. The reranker plays no part:
   without it the numbers are the same.
2. **A found file's passage often misses the answer.** The passage is the
   first 1,500 characters of the file's closest chunk. A page chunk is up to
   about 2,350 characters long and the whole-file chunk about 8,300. For 4 of
   the 7 files whose passage missed the answer, the answer was in the chosen
   chunk, behind the cut.

## Goals

- With reading on, engram questions do as well as with reading off.
- Files keep being found, and their passage shows the part that holds the
  question's words.
- No new model call and no noticeable extra time per search.

## Not goals

- Changing search with reading off. Everything here applies only when reading
  is on, so a deploy changes nothing for anyone until the switch.
- Choosing another chunk of the file by words (see Approaches, part 2, B).
- Removing near-copies of a document from the results. Versions of one
  document differ in text; showing an identical passage once changed nothing
  in the measurement.

## What would make it a failure

- An engram that reading off shows in the first five is missing from the
  first five with reading on, because of files.
- A passage that no longer holds what it holds today: a file whose passage
  held the answer before must still hold it.
- A search that fails or slows down because of the new steps.

## Approaches

### Part 1: the order

Simulated on the real rankings of the 60 questions:

| Approach | Engrams found | Files found |
|---|---|---|
| As it is | 29 | 18 |
| **A. At most 3 files among every five results** | **38** | **18** |
| B. Rank each kind on its own, then merge by score | 31 | 18 |
| A and B together | 38 | 18 |
| C. Weigh files lower | not simulated | |

**Chosen: A, with a guard.** B adds nothing on top of A, and it would put the
best file near the top even when it matches badly: under A a file still has
to earn its place against the engrams. C needs a weight tuned on 60
questions, which will not hold for another account.

The guard: each ranking takes its first rows per kind, so files can never
push every engram out of the candidates. Without it, the limit would have no
engram left to move up in an account with more files.

### Part 2: the passage

Simulated on the 18 files found:

| Approach | Passage holds the answer | Lost against today |
|---|---|---|
| As it is | 11 | |
| **A. The same chunk, cut around the question's words** | **14** | 0 |
| B. Words choose the chunk across the whole file, then cut | 16 | 0 |
| C. A longer passage | not simulated | |

**Chosen: A.** It is what RUG-64 did for engrams, it is the smaller change,
and it clears the target. B lets words alone decide which page is shown,
which is risky when the question is in another language than the document; it
stays as the next step if the measurement asks for it. C sends more text to
the assistant on every search.

The simulation matched words simply. The build uses Postgres's own matching,
so the numbers may move by one or two.

## The design

Everything below applies only when reading is on.

### 1. Candidates per kind

- **By meaning:** the statement that ranks engrams and files together takes
  the `fusion_depth` closest engrams and the `fusion_depth` closest files,
  each list cut on its own, then orders them together by distance. The first
  rows are the same as today; more rows follow.
- **By words:** the keyword hits take their first rows per kind in the same
  way: up to the limit of engrams and up to the limit of files, in the
  keyword order. This holds on all three paths: with the fusion, with the
  reranker alone, and by words alone.
- A row's place in a ranking stays its place among engrams and files
  together. Nothing else in the fusion changes.

### 2. At most three files among every five results

- A new setting, `REEF_SEARCH_FILES_IN_FIVE`, default 3, from 0 to 5. 5 means
  no limit.
- The results are read in blocks of five places: 1 to 5, 6 to 10, and so on.
  A block takes at most that many files. A file over the limit waits for the
  next block and keeps its order among files. Engrams keep their order among
  engrams.
- When no engram is left, files fill the remaining places: the rule never
  leaves a place empty and never drops a result.
- It is applied to the order search is about to use: after the fusion, before
  the reranker takes its candidates, again after the reranker, and on the
  rows of a search by words alone. Applying it twice gives the same order as
  once.
- A search of engrams alone (`files=False`) has no file and is unchanged.

### 3. A file's passage follows the words

- A chunk that fits in the passage is shown whole, as today.
- For a longer chunk, Postgres marks the question's words in the chunk's own
  text, with the same matching and the same two languages as search. The
  passage then keeps the chunk's context lines (filename, description, page)
  and starts at the paragraph that holds the marked words, or at their line
  when the paragraph is too long, as an engram's passage does since RUG-64.
- **The rule that keeps it safe:** the passage holds the whole fragment
  Postgres marked, or it is the chunk's first 1,500 characters, as today.
  When Postgres marks nothing, or the fragment cannot be found in the chunk,
  it is the chunk's start.
- This needs the chunk's whole text, so search reads more of a found file
  than the first 1,500 characters: up to a chunk's length. It runs in the
  short transaction that already reads the files, in a savepoint: on any
  failure the passage is cut as today.
- The page a file hit names, and its snippet, do not change.

## Tests the build must have

- The block rule alone, as a pure function: order kept within each kind,
  files over the limit moved to the next block, no engram left, limits 0, 3
  and 5, and that applying it twice changes nothing.
- A search where more than `fusion_depth` files are closer to the question
  than the right engram, by meaning and by words: the engram is in the first
  five with reading on.
- A search by words alone with more matching files than the limit: the same.
- Reading off: every existing search test passes unchanged.
- A long chunk whose telling words lie behind the cut: the passage holds
  them. The same without any matching word: the passage is the chunk's start.
  A short chunk: unchanged. The whole-file chunk and the second part of a
  long page: both follow the words.
- The safety rule: a passage that held a given text today still holds it.

## How we will know it worked

`scripts/measure_recall.py` on the same 60 questions, with reading on, run by
the controller on production after the deploy:

- engram questions: at least 38 of 40;
- file questions found: at least 16 of 20;
- answer in the passage: at least 12 of 20;
- median time: at most 100 ms above reading off, measured back to back.

Then RUG-60 goes on with its step 9.

## Left to the builder

- The exact statements, as long as row security decides what is shown exactly
  as it does today, and no statement can fail a search: new statements run in
  savepoints like the ones beside them.
- Whether the block rule is one function used in three places or built into
  the order once.
- The setting's place in the runbook's table and the note in the runbook's
  section on reading files.
