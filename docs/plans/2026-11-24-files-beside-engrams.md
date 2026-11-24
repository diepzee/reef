# Files Beside Engrams in Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With file reading on, engrams keep their place in the first search results and a found file's passage shows the part that holds the question's words.

**Architecture:** Three changes in `src/reef/search.py`, all active only when reading is on. Each ranking takes its candidates per kind, so files cannot push engrams out. A pure function then reorders the results so that a block of five places holds at most three files. A file's passage is cut from its closest chunk around the words Postgres marks in that chunk's own text, as RUG-64 did for engrams.

**Tech Stack:** Python 3.13, Postgres full-text search (`ts_headline`), pgvector, Piccolo raw SQL, pytest through `just test-py`.

**Spec:** `docs/specs/2026-11-24-files-beside-engrams-design.md`

## Global Constraints

- Everything applies only when `get_settings().reading.enabled` is true. With reading off, every statement search sends and every result it returns is unchanged, and every existing test passes unchanged.
- The new setting is `REEF_SEARCH_FILES_IN_FIVE`, default `3`, from `0` to `5`.
- No new statement may fail a search. A new statement that reads a table runs in a savepoint like `_closest` and `_nearest` do, and one that reads none is caught the same way; on any failure search answers as it does today, and logs a fixed text that holds nothing of the query, a filename or a text.
- Row security decides what is shown exactly as today: result rows still pass through `_chosen_rows`, and every table read runs under `arm(principal)`.
- A passage is at most `PASSAGE_CHARS` (1,500) characters. A file's passage holds the whole fragment Postgres marked, or it is the chunk's first `PASSAGE_CHARS` characters, as today.
- No new model call. No new dependency. `uv.lock` does not change.
- Modern Python with type hints. Every function, test functions included, has a ReST docstring without types that says exactly what the code does.
- Run tests with `just test-py <files>`, never `pytest` directly. Run `just lint` before each commit.
- End every commit message with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Review Focus

- **A limit that is not a multiple of five** (3, 7, 12): the last block is shorter. A reasonable person expects the first three results to hold at most three files and no place to stay empty.
- **Only files match, or only one engram matches:** the results are as long as before; files fill the places engrams cannot.
- **A search of one space** (`space=`): the per-kind limits keep the space filter in both branches.
- **An exact query** (a quoted phrase or an exclusion) with reading on: it runs by words alone; the block rule still holds, and a file without a closest chunk still shows its snippet.
- **A chunk whose marked fragment crosses the cut, or holds the mark character, or sits in a paragraph longer than a passage:** the passage holds the fragment whole or is the chunk's start, never half a fragment.

---

### Task 1: The block rule and its setting

**Files:**
- Modify: `src/reef/config.py` (class `SearchSettings`, after `semantic_weight`)
- Modify: `src/reef/search.py` (new function `spread`, placed after `fuse`)
- Modify: `docs/runbook.md` (settings table, regenerated)
- Test: `tests/test_search_spread.py` (new)

**Interfaces:**
- Produces: `search.spread(ranking: list[T], is_file: Callable[[T], bool], files_in_five: int) -> list[T]`, a pure function. Task 2 calls it with `(kind, id)` tuples and with result-row dicts.
- Produces: `get_settings().search.files_in_five: int`.

- [ ] **Step 1: Write the failing tests** in `tests/test_search_spread.py`

```python
"""The block rule: at most so many files among every five results."""

import random

from reef.search import spread


def E(number: int) -> tuple[str, int]:
    """Name an engram row.

    :param number: its place among engrams
    :returns: the row
    """
    return ("page", number)


def F(number: int) -> tuple[str, int]:
    """Name a file row.

    :param number: its place among files
    :returns: the row
    """
    return ("file", number)


def is_file(row: tuple[str, int]) -> bool:
    """Tell whether a row is a file.

    :param row: a row
    :returns: True for a file
    """
    return row[0] == "file"


def test_files_over_the_limit_wait_for_the_next_block():
    """Five files in front leave two places of the first five to engrams."""
    ranking = [F(1), F(2), F(3), F(4), F(5), E(1), E(2), F(6), E(3)]
    assert spread(ranking, is_file, 3) == [
        F(1), F(2), F(3), E(1), E(2), F(4), F(5), F(6), E(3),
    ]


def test_files_fill_the_places_no_engram_is_left_for():
    """With one engram, the fifth place goes to a file: no place stays empty."""
    ranking = [F(1), F(2), F(3), F(4), E(1)]
    assert spread(ranking, is_file, 3) == [F(1), F(2), F(3), E(1), F(4)]
    only_files = [F(n) for n in range(1, 8)]
    assert spread(only_files, is_file, 3) == only_files


def test_engrams_alone_and_an_order_within_the_limit_are_unchanged():
    """Nothing moves when no block holds more files than the limit."""
    engrams = [E(n) for n in range(1, 8)]
    assert spread(engrams, is_file, 3) == engrams
    mixed = [E(1), F(1), E(2), F(2), F(3), E(3), F(4)]
    assert spread(mixed, is_file, 3) == mixed


def test_the_limits_at_both_ends():
    """Five is no limit; zero puts every engram before every file."""
    ranking = [F(1), E(1), F(2), E(2), F(3), F(4), F(5), F(6)]
    assert spread(ranking, is_file, 5) == ranking
    assert spread(ranking, is_file, 0) == [
        E(1), E(2), F(1), F(2), F(3), F(4), F(5), F(6),
    ]


def test_any_order_keeps_each_kind_in_order_and_settles_at_once():
    """For random orders: no row lost, each kind keeps its order, every block
    holds at most the limit of files while engrams remain, and a second pass
    changes nothing."""
    rng = random.Random(77)
    for _ in range(300):
        rows = [E(n) for n in range(rng.randint(0, 12))]
        rows += [F(n) for n in range(rng.randint(0, 30))]
        rng.shuffle(rows)
        for limit in range(6):
            out = spread(rows, is_file, limit)
            assert sorted(out) == sorted(rows)
            assert [r for r in out if not is_file(r)] == [r for r in rows if not is_file(r)]
            assert [r for r in out if is_file(r)] == [r for r in rows if is_file(r)]
            assert spread(out, is_file, limit) == out
            for start in range(0, len(out), 5):
                block = out[start : start + 5]
                later_engram = any(not is_file(r) for r in out[start + len(block) :])
                if later_engram:
                    assert sum(map(is_file, block)) <= limit
```

- [ ] **Step 2: Run them and see them fail**

Run: `just test-py tests/test_search_spread.py`
Expected: FAIL, `ImportError: cannot import name 'spread'`.

- [ ] **Step 3: Add the setting** to `SearchSettings` in `src/reef/config.py`:

`files_in_five: int = Field(3, ge=0, le=5, description="With reading on, the most files among every five results. 5 is no limit.")`

Its variable is `REEF_SEARCH_FILES_IN_FIVE` through the group's prefix. Then run `just settings-table` so `docs/runbook.md` gets its row.

- [ ] **Step 4: Implement `spread`** in `src/reef/search.py`

Signature: `def spread[T](ranking: list[T], is_file: Callable[[T], bool], files_in_five: int) -> list[T]`.

The rule, exactly: places are filled one by one, in blocks of five (places 1 to 5, 6 to 10, …). For each place take the first row not yet placed, in the ranking's order. If that row is a file, the current block already holds `files_in_five` files, and an engram is still unplaced, take the first unplaced engram instead. The docstring states this rule and that the function is pure.

- [ ] **Step 5: Run the tests and the settings tests**

Run: `just test-py tests/test_search_spread.py tests/test_settings.py`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add src/reef/config.py src/reef/search.py docs/runbook.md tests/test_search_spread.py
git commit -m "feat(search): a rule for how many files five results may hold (RUG-77)"
```

---

### Task 2: Candidates per kind, and the rule in every search with reading on

**Files:**
- Modify: `src/reef/search.py`: `_hits_sql`, `_PAGE_HITS_SQL`, `_SEMANTIC_FILES_SQL`, `_closest`, `search_engrams`
- Test: `tests/test_search_files.py`

**Interfaces:**
- Consumes: `spread(ranking, is_file, files_in_five)` and `get_settings().search.files_in_five` from Task 1.
- Produces: no new public name. `search_engrams` keeps its signature.

What changes, and only with reading on (`file_text` true in `search_engrams`):

1. **By meaning.** `_SEMANTIC_FILES_SQL` cuts each half on its own: the `fusion_depth` closest engrams and the `fusion_depth` closest files, then both ordered together by distance, then id. `_closest` passes the depth to both halves. `_SEMANTIC_SQL` (reading off, or `files=False`) is untouched.
2. **By words.** `_PAGE_HITS_SQL` cuts each branch of its `UNION ALL` on its own, each `ORDER BY every_word DESC, rank DESC, at DESC LIMIT {}`, instead of one limit over both. `_HITS_SQL` (reading off) keeps its single limit and its text. All three paths that use `_PAGE_HITS_SQL` pass the limit for both branches: the fusion (`_RANKING_SQL`), the reranker alone (`_KEYWORD_RANKING_SQL`) and words alone (`_PAGE_RESULTS_SQL`).
3. **The rule.** In `search_engrams`, with reading on:
   - words alone: `rows = spread(rows, …)[:limit]` before `_record` and before the engrams' text is read;
   - otherwise: `ranking = spread(ranking, …)` after the fusion (or after the keyword ranking of the reranker-alone path), before the reranker's candidates are taken; and once more after `reorder`, before `chosen = ranking[:limit]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_search_files.py`, with the file's own helpers (`_file`, `_engram`, `_files`, `principal_for`, the `service` and `reranker` fixtures, `SubjectEmbedder`)

`SubjectEmbedder` gives a text the vector of the subject its words name; read it in `tests/test_search_hybrid.py` before writing the tests, and use its subjects as the existing tests here do ("car" finds "vehicle").

```python
async def test_files_cannot_push_the_right_engram_out_of_the_first_five(
    tx, household, service
):
    """More files than the fusion is deep, all as close to the question as
    the engram and all holding its word: the engram is still in the first
    five, which hold at most three files."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The vehicle is due in May.")
    await _engram(me, service, "personal", "garage.md", "The vehicle garage is in Ghent.")
    depth = get_settings().search.fusion_depth
    for number in range(depth + 5):
        file = await _file(
            me, household["w_personal"], f"garage-{number}.pdf", "A garage bill.",
            [f"The vehicle went in, vehicle check, vehicle bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5, passages=True)
    assert [hit["kind"] for hit in hits].count("file") == 3
    assert {"auto.md", "garage.md"} <= {hit.get("path") for hit in hits}


async def test_by_words_alone_files_leave_room_for_engrams(tx, household):
    """Without an embedding service, six files that hold the word and two
    engrams that hold it: the first five hold both engrams."""
    me = principal_for(household["wouter"])
    for path in ("auto.md", "garage.md"):
        await save_engram(me, "personal", path, "The xylophone is due in May.", message="x")
    for number in range(6):
        await _file(
            me, household["w_personal"], f"recital-{number}.pdf", "A programme.",
            ["The xylophone recital starts at eight. xylophone xylophone"],
        )
    hits = await search_engrams(me, "xylophone", limit=5)
    assert [hit["kind"] for hit in hits].count("file") == 3
    assert {"auto.md", "garage.md"} <= {hit.get("path") for hit in hits}


async def test_the_rule_holds_after_the_reranker(tx, household, service, reranker):
    """A reranker that likes every passage the same leaves the order, and the
    first five still hold at most three files."""
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The vehicle is due in May.")
    await _engram(me, service, "personal", "garage.md", "The vehicle garage is in Ghent.")
    for number in range(8):
        file = await _file(
            me, household["w_personal"], f"garage-{number}.pdf", "A garage bill.",
            [f"The vehicle went in for its yearly check, bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5, passages=True)
    assert [hit["kind"] for hit in hits].count("file") <= 3
    assert {"auto.md", "garage.md"} <= {hit.get("path") for hit in hits}


async def test_only_files_still_fill_the_results(tx, household, service):
    """With no engram to show, five files are five results."""
    me = principal_for(household["wouter"])
    for number in range(6):
        file = await _file(
            me, household["w_personal"], f"garage-{number}.pdf", "A garage bill.",
            [f"The vehicle went in for its yearly check, bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5)
    assert [hit["kind"] for hit in hits] == ["file"] * 5


async def test_the_setting_decides_how_many_files(tx, household, service, monkeypatch):
    """With the setting at 5 the files take every place, as before the rule."""
    monkeypatch.setattr(get_settings().search, "files_in_five", 5)
    me = principal_for(household["wouter"])
    await _engram(me, service, "personal", "auto.md", "The vehicle is due in May.")
    for number in range(6):
        file = await _file(
            me, household["w_personal"], f"garage-{number}.pdf", "A garage bill.",
            [f"The vehicle went in, vehicle check, vehicle bill {number}."],
        )
        await embedding.embed_file(me, file, service)
    hits = await search_engrams(me, "vehicle", limit=5)
    assert [hit["kind"] for hit in hits] == ["file"] * 5
```

Each of these must fail before the change for the reason its docstring gives: before it, the files hold every one of the first five places. If a fixture detail differs (for example the fake model's subjects, or how the files rank against the engrams by words), keep each test's claim and adapt its texts until it fails for that reason; never weaken an assertion.

Also add `test_with_reading_off_the_statements_are_as_before`: with the `reading` fixture's switch set back to off inside the test, capture every statement `Engram.raw` receives during one search with `files=True` and assert none of them is `search._SEMANTIC_FILES_SQL` or starts with the text built from `search._PAGE_HITS_SQL`, and that `spread` is not called (monkeypatch `search.spread` to raise).

- [ ] **Step 2: Run them and see them fail**

Run: `just test-py tests/test_search_files.py`
Expected: the five new search tests FAIL on their file counts or on the missing engram; the reading-off test passes already.

- [ ] **Step 3: Implement the three changes** listed above. `_hits_sql` gains what it needs to build the per-branch form for `_PAGE_HITS_SQL` while `_HITS_SQL` stays byte for byte what it is; assert that in a test if the builder changes its code path.

- [ ] **Step 4: Run the search tests**

Run: `just test-py tests/test_search_files.py tests/test_search_hybrid.py tests/test_search.py tests/test_search_spread.py tests/test_method_file_text.py`
Expected: PASS, the existing tests unchanged. `test_a_long_file_is_read_only_where_its_passage_is` still passes with its bounds.

- [ ] **Step 5: Lint, run the whole suite once, commit**

```bash
just lint
just test-py
git add src/reef/search.py tests/test_search_files.py
git commit -m "fix(search): files leave room for engrams in the first results (RUG-77)"
```

---

### Task 3: A file's passage follows the words

**Files:**
- Modify: `src/reef/search.py`: `_read_files`, `_file_passages`, `_payloads`, and a new helper for the chunk's marked fragment
- Modify: `docs/runbook.md` (section "Reading files (RUG-37)": two sentences on the order and the passage)
- Test: `tests/test_search_files.py`

**Interfaces:**
- Consumes: `_plain`, `_seeker`, `_PARAGRAPH_BREAK`, `_GAP`, `_headline`, `_QUERY_SQL`, `filechunks.whole`, `filechunks.page_chunks`, `sections.Section` (`text` is the context lines, a blank line, then `body`).
- Produces: no new public name.

Behaviour:

1. A chunk whose text is at most `PASSAGE_CHARS` long is the passage, as today.
2. For a longer chunk, search asks Postgres for the headline of the chunk's **body**, with `_headline` and the query relation of `_QUERY_SQL`, the texts passed as an array parameter with `unnest … WITH ORDINALITY`, never by string formatting. One statement for all such chunks of a search. It reads no table, so it may run in a savepoint of the transaction that reads the files or in a short transaction of its own; either way, on any failure every passage is cut as today.
3. The fragment is found whole in the body with `_seeker(_plain(fragment))`. The passage is the chunk's context lines, then `_GAP` when the shown text does not start at the body's start, then the body from the start of the paragraph that holds the fragment, or from the start of its line when the paragraph would push the fragment past the cut; all cut to `PASSAGE_CHARS`.
4. **The rule:** if the result's plain text does not hold the fragment's plain text in full, or Postgres marked nothing (the headline holds no mark character), the passage is `chunk.text[:PASSAGE_CHARS]`, as today.
5. For the whole-file chunk, `_read_files` reads the start of the text up to `get_settings().embedding.engram_chars` instead of `PASSAGE_CHARS`, so the chunk is whole. Page chunks are already read whole.
6. The hit's `page` and `snippet` do not change.

- [ ] **Step 1: Write the failing tests** in `tests/test_search_files.py`

```python
#: A page chunk longer than a passage whose telling sentence lies behind the
#: cut: one paragraph of filler, then the sentence in a paragraph of its own.
BURIED = "The vehicle papers. " + "word " * 330 + "\n\nThe vehicle inspection sticker is number QX-7731.\n\n" + "word " * 20


async def test_a_long_chunk_is_cut_around_the_questions_words(tx, household, service):
    """The words asked for lie behind the first 1,500 characters of the
    closest chunk: the passage keeps the chunk's context lines and starts at
    their paragraph."""
    me = principal_for(household["wouter"])
    file = await _file(
        me, household["w_personal"], "papers.pdf", "Car papers.",
        [f"Holiday plans. {FILLER}", BURIED],
    )
    await embedding.embed_file(me, file, service)
    [hit] = _files(await search_engrams(me, "vehicle inspection sticker", passages=True))
    assert "QX-7731" in hit["passage"]
    assert hit["passage"].startswith("papers.pdf\nCar papers.\npage 2\n\n…\n\n")
    assert len(hit["passage"]) <= PASSAGE_CHARS
    assert hit["page"] == 2


async def test_a_long_chunk_without_the_questions_words_starts_at_its_start(
    tx, household, service
):
    """Found by meaning alone, with no word of the question in the chunk: the
    passage is the chunk's first 1,500 characters, as before."""
    me = principal_for(household["wouter"])
    file = await _file(
        me, household["w_personal"], "papers.pdf", "Car papers.",
        [f"Holiday plans. {FILLER}", BURIED],
    )
    await embedding.embed_file(me, file, service)
    [hit] = _files(await search_engrams(me, "car", passages=True))
    chunks = filechunks.split("papers.pdf", "Car papers.", [(1, f"Holiday plans. {FILLER}"), (2, BURIED)])
    closest = next(chunk for chunk in chunks if chunk.anchor == "p2")
    assert hit["passage"] == closest.text[:PASSAGE_CHARS]


async def test_the_whole_file_chunk_follows_the_words_too(tx, household, service):
    """A file of one long page: its whole-file chunk is closest, and the
    passage shows the paragraph behind the cut that holds the words."""
    me = principal_for(household["wouter"])
    file = await _file(me, household["w_personal"], "papers.pdf", "Car papers.", [BURIED])
    await embedding.embed_file(me, file, service)
    [hit] = _files(await search_engrams(me, "vehicle inspection sticker", passages=True))
    assert "QX-7731" in hit["passage"]
    assert len(hit["passage"]) <= PASSAGE_CHARS


async def test_a_failing_headline_of_the_chunks_leaves_the_start(
    tx, household, service, monkeypatch
):
    """When the statement that marks the words fails, the search answers and
    the passage is the chunk's start."""
    me = principal_for(household["wouter"])
    file = await _file(
        me, household["w_personal"], "papers.pdf", "Car papers.",
        [f"Holiday plans. {FILLER}", BURIED],
    )
    await embedding.embed_file(me, file, service)
    # Break exactly the new statement: name its constant here once it exists.
    monkeypatch.setattr(search, "_CHUNK_HEADLINES_SQL", "SELECT nothing FROM nowhere")
    [hit] = _files(await search_engrams(me, "vehicle inspection sticker", passages=True))
    assert hit["passage"].startswith("papers.pdf\nCar papers.\npage 2\n\nThe vehicle papers.")
```

Name the new statement `_CHUNK_HEADLINES_SQL` so the last test can break it. Add one pure test of the cutting helper for the rule in point 4: a fragment that crosses the cut gives `chunk.text[:PASSAGE_CHARS]`.

- [ ] **Step 2: Run them and see them fail**

Run: `just test-py tests/test_search_files.py`
Expected: the first and third new tests FAIL (`QX-7731` not in the passage); the second passes already; the fourth fails on the missing constant.

- [ ] **Step 3: Implement** points 1 to 6. Cutting stays in a thread, as `_file_passages` runs today. No database connection waits on anything but its own statements.

- [ ] **Step 4: Run the search tests**

Run: `just test-py tests/test_search_files.py tests/test_search_hybrid.py tests/test_search.py tests/test_method_file_text.py`
Expected: PASS. `test_a_long_file_is_read_only_where_its_passage_is` keeps its bounds: a page chunk reads no more pages than before.

- [ ] **Step 5: Document.** In `docs/runbook.md`, section "Reading files (RUG-37)", add in plain words: with reading on, five results hold at most three files (`REEF_SEARCH_FILES_IN_FIVE`), and a file's passage is cut from its closest chunk around the question's words.

- [ ] **Step 6: Lint, run the whole suite once, commit**

```bash
just lint
just test-py
git add src/reef/search.py tests/test_search_files.py docs/runbook.md
git commit -m "fix(search): a file's passage follows the question's words (RUG-77)"
```

---

## After the tasks

The controller, not a task: open the pull request with the `no-changelog` label (nothing changes for anyone while reading is off), and after Wouter's merge and the deploy, run `scripts/measure_recall.py` on production for the 60 private questions with reading on and off. The bar is in the spec.
