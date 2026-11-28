# Priming, spreading and a recall path: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Search can lift a match that the session's earlier searches returned or that a good match links to, says so in the result, and can be measured on test sets, with both switches off by default.

**Architecture:** One pure module computes weights and the new order from plain lists. Two small readers fetch what it needs as the person: the session's earlier results from the search log, and the links around the candidates. `search_engrams` calls the stage at one point, after the reranker and before replaced engrams step back. `recall` and `brief` pass the stage's note on as `why`.

**Tech Stack:** Python 3.13, Postgres with row-level security (Piccolo raw SQL), FastMCP, pytest through `just test-py`.

**Spec:** `docs/specs/2026-11-28-priming-spreading-design.md`. Background: `docs/specs/memory-engine.md`, section 3.2.

## Global Constraints

- Both switches off: every search runs the statements it runs today and returns what it returns today. No extra query.
- No migration. Nothing new is stored. Priming reads `search_log`; it writes nothing.
- Every read runs as the person, under row security, inside the search's own transaction.
- The stage never makes a search fail. If reading the log or the links fails, the order stays as it was and no result carries `why`. Use a savepoint, as `_replacement_order` does.
- A question with a quoted phrase or an exclusion (`_EXACT`) gets no lift.
- Only engrams get weight. Files keep their place among the candidates and are never primed.
- Nothing is passed into an engram that has been replaced (the target of an active `supersedes` link).
- Only candidates are sorted. An engram that is not a candidate never enters.
- Exact values: 3 engrams per earlier search, 10 primed engrams at most, 60 minutes, weight 0.5 for the newest search and half for each one back, 20 sources, two rounds, half passed on per round, 50 candidates, both lifts 0.3 by default.
- Settings: `REEF_SEARCH_PRIMING` and `REEF_SEARCH_SPREADING` (booleans, default false), `REEF_SEARCH_PRIMING_LIFT` and `REEF_SEARCH_SPREADING_LIFT` (floats, default 0.3, at least 0).
- No query text, engram text, path or person in any log line or Logfire span.
- Python: modern types, a ReST docstring on every function, no types in docstrings. Plain language in every text an assistant or a person reads.
- The connector's `search` keeps its exact shape: `id`, `title`, `url`.

## Review Focus

1. **Both switches off.** A search by words alone still runs its single statement and cuts at the asked number. Test: with both off, the statements issued and the results equal those of a build without the stage, on all three routes.
2. **Another member's session.** In a shared space, member B's logged searches never give weight in member A's search, also when both use the same session label. Test with two members and one made label.
3. **A link the person cannot read.** A link whose other end is in a space the person is not in passes no weight and never appears in `linked_from`.
4. **The stage breaks.** The log read or the link read raises. The search answers in its old order, without `why`, and the transaction is still usable.
5. **A change of subject.** A primed engram that does not match the new question is not a candidate and does not appear. One that matches poorly rises by no more than the lift allows: at lift 0.3, place 7 stays out of the first five.

Each of these has its test in the task that owns the code (tasks 1, 3 and 4).

---

### Task 1: The stage, as a pure module

**Files:**
- Create: `src/reef/lifting.py`
- Test: `tests/test_lifting.py`

**Interfaces:**
- Produces:
  - `PRIMED_PER_SEARCH = 3`, `PRIMED_ENGRAMS = 10`, `PRIMING_MINUTES = 60`, `SPREAD_SOURCES = 20`, `CANDIDATES = 50`, `NEWEST_WEIGHT = 0.5`, `PASSED_ON = 0.5`, `LINKED_FROM = 3`
  - `primed_weights(earlier: list[list[UUID]]) -> dict[UUID, float]`: `earlier` is one list of engram ids per earlier search, newest search first, each best first.
  - `linked_weights(sources: dict[UUID, float], neighbours: dict[UUID, list[UUID]], closed: set[UUID]) -> tuple[dict[UUID, float], dict[UUID, dict[UUID, float]]]`: the weight each engram received, and per receiver how much came from which source of the first round. `closed` are replaced engrams, which receive nothing and pass nothing on.
  - `@dataclass(frozen=True) class Why`: `place_by_match: int`, `primed: bool`, `linked_from: tuple[UUID, ...]`
  - `lift(candidates: list[tuple[str, UUID]], primed: dict[UUID, float], linked: dict[UUID, float], origins: dict[UUID, dict[UUID, float]], *, priming_lift: float, spreading_lift: float) -> tuple[list[tuple[str, UUID]], dict[UUID, Why]]`: the new order, and a `Why` for each engram that moved up.

- [ ] **Step 1: Write the failing tests**

```python
def test_newest_search_counts_half_and_each_one_back_half_of_that():
    a, b, c, d = (uuid4() for _ in range(4))
    weights = primed_weights([[a, b], [b, c], [d]])
    assert weights == {a: 0.5, b: 0.75, c: 0.25, d: 0.125}

def test_only_the_first_three_of_a_search_and_ten_engrams_in_all():
    ids = [uuid4() for _ in range(20)]
    weights = primed_weights([ids[0:5], ids[5:10], ids[10:15], ids[15:20]])
    assert set(weights) == set(ids[0:3] + ids[5:8] + ids[10:13] + ids[15:16])

def test_a_source_shares_half_its_weight_among_its_neighbours():
    a, b, c = uuid4(), uuid4(), uuid4()
    received, origins = linked_weights({a: 1.0}, {a: [b, c]}, set())
    assert received[b] == received[c] == 0.25
    assert origins[b] == {a: 0.25}

def test_the_second_round_passes_half_of_what_was_received():
    a, b, c = uuid4(), uuid4(), uuid4()
    received, origins = linked_weights({a: 1.0}, {a: [b], b: [a, c]}, set())
    assert received[b] == 0.5
    assert received[c] == 0.125          # half of 0.5, shared with a
    assert origins[c] == {a: 0.125}      # traced back to the first source

def test_a_source_gets_nothing_back_from_itself():
    a, b = uuid4(), uuid4()
    received, _ = linked_weights({a: 1.0}, {a: [b], b: [a]}, set())
    assert a not in received

def test_a_replaced_engram_receives_nothing_and_passes_nothing():
    a, old, c = uuid4(), uuid4(), uuid4()
    received, _ = linked_weights({a: 1.0}, {a: [old], old: [c]}, {old})
    assert old not in received and c not in received

def test_full_lift_of_point_three_moves_the_sixth_to_fifth_and_no_further():
    ids = [uuid4() for _ in range(8)]
    order, why = lift([("page", i) for i in ids], {ids[5]: 0.5}, {}, {},
                      priming_lift=0.3, spreading_lift=0.3)
    assert [i for _, i in order].index(ids[5]) == 4
    assert why[ids[5]] == Why(place_by_match=6, primed=True, linked_from=())
    assert ids[4] not in why      # pushed down by one: no note
    assert ids[0] not in why

def test_at_point_three_the_seventh_stays_out_of_the_first_five():
    ids = [uuid4() for _ in range(8)]
    order, _ = lift([("page", i) for i in ids], {ids[6]: 0.5}, {}, {},
                    priming_lift=0.3, spreading_lift=0.3)
    assert [i for _, i in order].index(ids[6]) >= 5

def test_shares_are_measured_against_the_largest_weight_of_each_kind():
    ids = [uuid4() for _ in range(10)]
    order, _ = lift([("page", i) for i in ids], {}, {ids[8]: 1.0, ids[9]: 0.1}, {},
                    priming_lift=0.3, spreading_lift=1.0)
    assert [i for _, i in order].index(ids[8]) == 4      # 1/9 * 2 beats 1/5
    assert [i for _, i in order].index(ids[9]) == 9      # 1/10 * 1.1 stays last

def test_files_keep_their_place_among_candidates():
    f, a, b = uuid4(), uuid4(), uuid4()
    order, why = lift([("file", f), ("page", a), ("page", b)], {f: 0.5}, {f: 1.0}, {},
                      priming_lift=1.0, spreading_lift=1.0)
    assert order == [("file", f), ("page", a), ("page", b)] and why == {}

def test_no_weight_means_the_same_order_and_no_notes():
    ids = [uuid4() for _ in range(5)]
    order, why = lift([("page", i) for i in ids], {}, {}, {},
                      priming_lift=0.3, spreading_lift=0.3)
    assert order == [("page", i) for i in ids] and why == {}

def test_linked_from_names_the_three_strongest_sources_strongest_first():
    target, s1, s2, s3, s4 = (uuid4() for _ in range(5))
    origins = {target: {s1: 0.1, s2: 0.4, s3: 0.3, s4: 0.2}}
    ids = [uuid4() for _ in range(5)] + [target]
    _, why = lift([("page", i) for i in ids], {}, {target: 1.0}, origins,
                  priming_lift=0.3, spreading_lift=0.3)
    assert why[target].linked_from == (s2, s3, s4)
```

- [ ] **Step 2: Run them and see them fail**

Run: `just test-py tests/test_lifting.py`
Expected: FAIL, the module does not exist.

- [ ] **Step 3: Write `src/reef/lifting.py`**

Pure: no database, no settings, no logging. Rules the tests do not fully fix:
- `primed_weights`: an engram returned by several searches gets the sum. Engrams are counted toward the ten in the order newest search first, best first; a repeat does not use up a place.
- `linked_weights`: round one passes `weight × PASSED_ON` of each source, shared equally among its neighbours that are not closed. Round two does the same from what each receiver got in round one. A source never receives from its own weight, in either round. `origins` always names sources of round one.
- `lift`: score is `(1 / place) × (1 + priming_lift × primed share) × (1 + spreading_lift × linked share)`, shares as in the spec. Sort with a stable sort. A `Why` is made only for an engram that has weight and ends at a better place than its place by match. One that was pushed down gets none. `primed` is true when it has primed weight.

- [ ] **Step 4: Run the tests and see them pass**

Run: `just test-py tests/test_lifting.py`
Expected: PASS.

- [ ] **Step 5: Commit**

`feat(search): a pure stage that lifts primed and linked matches (RUG-25)`

---

### Task 2: Settings

**Files:**
- Modify: `src/reef/config.py` (`SearchSettings`)
- Test: `tests/test_settings.py`, and `tests/test_config_prefix.py` if it pins names

**Interfaces:**
- Produces: `get_settings().search.priming: bool`, `.spreading: bool`, `.priming_lift: float`, `.spreading_lift: float`

- [ ] **Step 1: Tests first.** Defaults are `False`, `False`, `0.3`, `0.3`. `REEF_SEARCH_PRIMING=1` and `REEF_SEARCH_SPREADING=1` switch them on. A negative lift is refused.
- [ ] **Step 2: Run** `just test-py tests/test_settings.py` and see them fail.
- [ ] **Step 3: Add the four fields**, in the style of their neighbours. Each description says in one sentence what it does and that it is off, or a starting value, until the test sets decide.
- [ ] **Step 4: Run and see them pass.**
- [ ] **Step 5: Commit** `feat(search): switches and lifts for priming and spreading (RUG-25)`

---

### Task 3: Reading the session's earlier results and the links

**Files:**
- Modify: `src/reef/searchlog.py`, `src/reef/links.py`
- Test: `tests/test_searchlog.py`, `tests/test_links.py`

**Interfaces:**
- Produces:
  - `searchlog.earlier_results(principal: Principal, session_id: str) -> list[list[UUID]]`: the person's own logged searches with this label from the last `PRIMING_MINUTES`, newest first, each as its engram ids best first. At most 10 searches. Empty when the log is off.
  - `links.neighbourhood(engram_ids: list[UUID]) -> tuple[dict[UUID, list[UUID]], set[UUID]]`: for the given engrams and their direct neighbours, each one's neighbours over active links in both directions; and the set of engrams among all of these that have been replaced.

- [ ] **Step 1: Tests first**
  - `earlier_results`: returns the caller's searches of that label newest first; leaves out a search older than 60 minutes; leaves out another label; **leaves out another member's searches with the same label** (two members of one shared space); returns `[]` with `REEF_SEARCH_LOG` off.
  - `neighbourhood`: a wiki link counts in both directions; a superseded link does not count; **a link whose other end the person cannot read does not appear** (an engram in a space the person is not in); an engram with an active `supersedes` link pointing at it is in the replaced set; the second hop is included for neighbours of the given engrams and not beyond.
- [ ] **Step 2: Run** `just test-py tests/test_searchlog.py tests/test_links.py` and see them fail.
- [ ] **Step 3: Write both.** Each is one statement, run as the person inside the caller's transaction. `neighbourhood` is bounded: at most 2,000 link rows, taken in a fixed order. Neither logs anything.
- [ ] **Step 4: Run and see them pass.**
- [ ] **Step 5: Commit** `feat(search): read a session's earlier results and the links around matches (RUG-25)`

---

### Task 4: The stage inside search

**Files:**
- Modify: `src/reef/search.py` (`search_engrams`, `_row_payload`, and what they need)
- Test: `tests/test_search_lift.py` (new)

**Interfaces:**
- Consumes: Task 1, 2 and 3.
- Produces:
  - `search_engrams(..., earlier: list[list[UUID]] | None = None)`: when given, these are the session's earlier results (newest first) and the search log is not read. When `None` and priming is on, they come from `searchlog.earlier_results` with `usage.current_session_id()`; outside a session there are none.
  - An engram hit that the stage moved up carries `why`: `{"place_by_match": int, "primed": True (only when primed), "linked_from": [{"cove": alias, "path": path}, ...] (only when not empty)}`.

- [ ] **Step 1: Tests first**, with made engrams and links, on the route by words alone and on the route with the embedder fixture `tests/test_search_hybrid.py` uses:
  - spreading on: an engram linked from the best match moves up, and its hit says `linked_from` with that match;
  - priming on, `earlier` given: the engram moves up and its hit says `primed`;
  - priming on, no `earlier`, inside a made session with logged searches: the same, read from the log;
  - both on: a neighbour of a primed engram gets linked weight;
  - **both off: the same results as before the change, and on the words-only route the same single statement and the cut at the asked number** (count the statements, or compare against a recorded list);
  - a quoted phrase: no hit has `why`;
  - a replaced engram gets no lift and still steps back;
  - a file among the candidates keeps its place and is never in `linked_from`;
  - **the log read raises, then the link read raises: the old order, no `why`, and a later statement in the same transaction still works**;
  - the reranker route, with the reranker fixture and `REEF_RERANK_MIN_ENGRAMS=0`: the stage runs after the reranker's order.
- [ ] **Step 2: Run** `just test-py tests/test_search_lift.py` and see them fail.
- [ ] **Step 3: Wire it in.**
  - With a switch on and the question not exact, every route hands the stage up to `CANDIDATES` candidates: the words-only route takes its ranking form, as the reranker route already does; the fused ranking is cut to 50.
  - The stage runs once, after the reranker's `reorder`, before `_replacement_order`, and before the last `spread` of files and the cut. On the routes with two transactions it runs in the second.
  - Sources for spreading: the first `SPREAD_SOURCES` engram candidates with weight `1 / place`, plus the primed engrams with their weight when priming is on.
  - The readers run inside a savepoint. Any failure: log the error's class only, keep the order.
  - `linked_from` holds only engrams the person can read; their space alias and path are read with the chosen rows. Keep the hit's existing keys as they are (`cove`, `path`).
- [ ] **Step 4: Run** `just test-py tests/test_search_lift.py tests/test_search.py tests/test_search_hybrid.py tests/test_search_replaced.py tests/test_search_spread.py tests/test_search_files.py` and see them pass.
- [ ] **Step 5: Commit** `feat(search): lift primed and linked matches behind two switches (RUG-25)`

---

### Task 5: The recall path in `recall` and `brief`

**Files:**
- Modify: `src/reef/methods/find.py` (`_passage`, `brief`), `src/reef/protocol.md`, `src/reef/server.py` (the `recall` description, one clause at most)
- Test: `tests/test_method_find.py`, `tests/test_protocol.py`, `tests/test_tool_surface.py`

**Interfaces:**
- Consumes: the `why` of Task 4.
- Produces: a `recall` or `brief` item for an engram that was moved up carries `"why": {"place_by_match": 6, "primed": true, "linked_from": ["personal/projects/bathroom-renovation.md"]}`. `linked_from` holds references `open` takes. Keys that do not apply are left out. Every other item has no `why`.

- [ ] **Step 1: Tests first.** `recall` with spreading on returns `why` with `linked_from` as references; with both switches off no item has the key; `brief` passes it on the same way; the connector's `search` result is exactly `id`, `title`, `url` with both switches on; the protocol holds the new sentence; the MCP output schema still accepts the answers.
- [ ] **Step 2: Run** the three test files and see the new tests fail.
- [ ] **Step 3: Pass `why` on**, and add to `protocol.md`, near "Answer with `recall`", one or two short sentences: what `why` means, and that an assistant may tell the person why a result came up when it helps. Check how much `load_index` grows: at most 300 characters.
- [ ] **Step 4: Run and see them pass.**
- [ ] **Step 5: Commit** `feat(recall): a result says why its place changed (RUG-25)`

---

### Task 6: Measuring sessions and settings

**Files:**
- Modify: `scripts/measure_recall.py`
- Test: `tests/test_measure_recall.py`

**Interfaces:**
- Produces:
  - A question may carry `"set": "<name>"`. The output gains `"sets": {"<name>": {"asked": n, "found_at_5": n}}`. Questions without a set are counted as today, and today's output keys stay.
  - An item may be `{"session": [question, question, ...]}`. Its questions are asked in order. Each search gets the engram ids of the earlier searches of that item as `earlier`, newest first. No search log and no MCP session are needed.
  - Flags for this process only, like `--file-text`: `--priming on|off`, `--spreading on|off`, `--priming-lift FLOAT`, `--spreading-lift FLOAT`.

- [ ] **Step 1: Tests first.** A session's second question is searched with the first one's results as `earlier`; sets are counted apart; the flags set the settings for the process and nothing else; old question files give the old output plus an empty `sets`.
- [ ] **Step 2: Run** `just test-py tests/test_measure_recall.py` and see them fail.
- [ ] **Step 3: Write it.** The script still prints numbers only: no question, no reference, no passage.
- [ ] **Step 4: Run and see them pass.**
- [ ] **Step 5: Commit** `feat(eval): measure sessions, sets and the two switches (RUG-25)`

---

### Task 7: The public test sets

**Files:**
- Create: `tests/fixtures/recall_sets.json`
- Modify, only where the sets need it: `src/reef/testaccount/person/engrams/**`, and the fixture's version
- Test: `tests/test_recall_sets.py` (new), `tests/test_testaccount_fixture.py`

**Interfaces:**
- Consumes: the format of Task 6.
- Produces: one file in that format, for the made-up person, with three sets:
  - `linked`: at least 10 questions. Each has `"via"`: the engram that matches the question well and links to the expected one.
  - `follow_up`: at least 10 sessions of two questions. The second makes sense only after the first; its expected engram is the one the first was about.
  - `change_of_subject`: at least 5 sessions of two questions about different things; the second's expected engram has nothing to do with the first.

- [ ] **Step 1: Write the checking tests first.** Every expected reference and every `via` exists in the fixture. For every `linked` question, `via` links to the expected engram or the other way round, in the fixture's own text. Every `follow_up` second question shares no content word with its expected engram's title (so words alone cannot answer it). The counts meet the minimums.
- [ ] **Step 2: Run** `just test-py tests/test_recall_sets.py` and see them fail.
- [ ] **Step 3: Write the questions**, in Dutch like the fixture. Use the links the fixture has. Add an engram or a link only where a set cannot be made without it; if you do, raise the fixture's version, follow what `tests/test_testaccount_fixture.py` and the scenarios pin, and say so in the pull request.
- [ ] **Step 4: Run** `just test-py tests/test_recall_sets.py tests/test_testaccount_fixture.py tests/test_testaccount_reset.py tests/test_conversations_scoring.py` and see them pass.
- [ ] **Step 5: Commit** `test(eval): public sets for linked answers, follow-ups and changes of subject (RUG-25)`

---

### Task 8: Docs

**Files:**
- Modify: `scripts/eval/README.md`, `docs/runbook.md`

- [ ] **Step 1:** In the eval README, a section "Priming and spreading": the three sets, how to run them against a local Reef with an embedding service (`scripts/measure_recall.py` with the new flags), the rule for passing from the spec, and an empty results table with one row per setting to fill in.
- [ ] **Step 2:** In the runbook, the four settings, that both switches are off, and that switching priming on needs the search log and one more sentence on the privacy page first.
- [ ] **Step 3: Commit** `docs(eval): how to measure priming and spreading (RUG-25)`

---

## After the build (not for the builder)

1. Draft the private sets from Wouter's export; he checks them.
2. Measure: both off, each on at lifts 0.3, 0.5 and 1.0, both on. On the public sets locally and against production's test account, on the private sets locally, and the existing 40 and 20 questions.
3. Report with a recommendation per mechanism. Switching on is RUG-38 and Wouter's decision.
