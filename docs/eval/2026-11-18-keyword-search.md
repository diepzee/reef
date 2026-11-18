# What improved keyword search (18 November 2026)

Search now finds the right page for 53% of benchmark questions.
It found 24% before.
This report lists each change, what it gained, and what was tried and dropped.

The changes are in pull request #131.
How to run the benchmark is in [`scripts/eval/README.md`](../../scripts/eval/README.md).

## How it was measured

- **Benchmark:** LongMemEval-S, cleaned version. 100 questions over 4,546 pages.
- **Found:** a page that holds the answer is in the first five results.
- **All needed pages:** every page the question needs is in the first five results.
- **Rank score (MRR):** how high the first right page stands, over the first ten results. 1.0 means always first.

## The result

| Step | Found | All needed pages | Rank score |
|---|---|---|---|
| Search as it was | 24.0% | 10.0% | 0.195 |
| 1. Any word may match | 50.0% | 22.0% | 0.393 |
| 2. Scale rank by page length | 53.0% | 24.0% | 0.437 |

## Change 1: any word may match (+26 points)

- **Before:** a page had to contain every word of the query. One missing word ruled it out.
- **Now:** a page that contains some of the words matches. Pages that contain all of them still come first.
- **Why it helps:** a question in ordinary language rarely shares all its words with the page that answers it.
- **What stays strict:** an excluded word (`-word`) and a quoted phrase work as before.

## Change 2: scale rank by page length (+3 points)

- **Before:** a long page outranked a short one just by repeating a common word.
- **Now:** the rank is divided by the logarithm of the page length (`ts_rank` normalization 1).
- **Why it helps:** the first right page moves up. The rank score rose more than "found" did.

## Tried and dropped

| Variant | Found | Why not |
|---|---|---|
| Divide rank by the full page length (normalization 2) | 22% | Punishes long pages far too hard |
| Divide rank by the number of unique words (normalization 8) | 22% | Same problem |
| Cover-density ranking (`ts_rank_cd`) | 32% to 34% | Rewards words that stand close together. In a question they rarely do |
| Order by the number of query words present | 52% | No better, and about 1.2 seconds per query |

## By question type

Share of questions where a right page is in the first five results.

| Type | Before | Now |
|---|---|---|
| Knowledge update | 67% | 73.3% |
| Multi-session | 8% | 50.0% |
| Assistant said it | 0% | 75.0% |
| Preference | 0% | 0.0% |
| User said it | 43% | 42.9% |
| Temporal | 22% | 51.8% |

## What did not move

- **Preference questions stay at 0%.** The question and the page share no words. Keyword search cannot fix that. Embeddings can.
- **"All needed pages" is still 24%.** A question that needs two or three pages rarely gets all of them in five results.

## Cost and limits

- **Speed:** a search on the 4,546-page benchmark takes about 160 ms. It took about 50 ms.
- **English only.** The benchmark has no Dutch. None of this is proven for Dutch pages.
- **One run, 100 questions.** A difference of 3 points is three questions. Change 1 is solid. Change 2 is likely real, but small.

## Also in the same change

- **Regression gate:** the test suite runs a small slice of the benchmark. Its floors now sit at the new scores, so a change that undoes this fails the build.
- **Bug fix:** a NUL byte in a query made the search fail. It is now stripped.

## What comes next

**53.0% is the line that embeddings have to beat.**
Hybrid search (RUG-35) is measured against it, not against 24%.
