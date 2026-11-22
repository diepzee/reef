# Test Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an Orca Codex worker on Astra hold scripted conversations with Reef's tools on a made-up person, locally and in production, each run starting from the same memory.

**Architecture:** A `test_accounts` table, set only by an operator, marks made-up people. A server reset (`reef.testaccount`) wipes such a person's memory and reloads a fixture shipped in the package, reached by a bearer-authenticated web route and a `reef reset-test-account` CLI command. A runner (`scripts/conversations/`) resets, plays TOML scenarios through `codex exec` on `gpt-6-astra`, reads tool calls from Codex's JSON events, and writes a report.

**Tech Stack:** Python 3.13, FastMCP 3.4.6, Starlette routes, Piccolo with fixed-SQL migrations, Postgres 18 with FORCE row-level security, `tomllib`, Codex CLI 0.167 (`codex exec --json`, `codex exec resume`), Orca orchestration.

**Spec:** `docs/specs/2026-11-22-test-conversations-design.md`

## Global Constraints

- Modern Python with type hints; every function, class and module has a ReST docstring with no types in it.
- Migrations carry fixed SQL and never import `reef.rls`; `tests/test_migration_chain.py` must pass with the new statements added to its list.
- New tables have `ENABLE` and `FORCE ROW LEVEL SECURITY`; definer functions are built with `_function_ddl` and owned by `reef_authz`.
- Only an operator, directly in the database, can flag a test account: no policy, tool, route or request may insert into `test_accounts`.
- The reset refuses unless the caller is flagged, is a member of no space someone else owns, and none of their spaces has another member.
- The reset is never an MCP tool.
- A confirmation token issued before a reset must not work after it.
- No engram text, query, reason, filename, description, email or person id goes to Logfire or the logs.
- Everything in the fixture and scenarios is invented: no real person's name, address, employer or documents.
- Text people read (persona, scenario messages, report, runbook) follows plain language.
- The model is `gpt-6-astra`; every scenario runs in a fresh `codex exec` session.
- Reports are written outside git, under `~/.reef-conversations/reports/`.
- Run the suite with `just test-py` (it holds the machine-wide test lock); lint with `just lint`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings made while planning

- The fixture lives in the package, `src/reef/testaccount/person/`, not `tests/conversations/person/` as the spec says, so the server image carries it. Scenarios stay in `tests/conversations/`.
- Fixture front matter and scenarios are TOML (`+++` front matter, `scenarios.toml`), read with `tomllib`: no new dependency. The spec said YAML for scenarios.
- No statistics across people read the search log today (`searchlog.misses` reads one person's own rows), so only `reef_call_log_totals` changes. Its docstring records the rule for any future statistic.
- "Whether search fell back" is reported as which search services the server has configured, from the reset's answer. Per-call fallback is not tracked.
- The loader saves with `channel="system"` and `via=None`, so loading records no usage and every reset starts with no activation.
- The reset deletes the person's queued jobs, their `embedding_spend` and `reading_spend` rows, and moves the reload's jobs to run at once. Each reset would otherwise wait 120 seconds for embedding and eat the daily allowance.

## Review Focus

1. A real account calls the reset: it refuses with `not_a_test_account` and deletes nothing. (Task 4)
2. A flagged account shares a space with someone: it refuses with `shared_with_others` and deletes nothing. (Task 4)
3. Two resets at once: the second waits, and the account ends with exactly one copy of the fixture. (Task 4)
4. A file left half-stored (a `pending` attachment) or a job left queued before the reset: both are gone after it, and readiness still turns true. (Task 4)
5. A scenario's assistant runs a shell command or reads a file instead of using Reef: the scenario is marked `invalid`, never `pass`. (Task 8)

---

### Task 1: Flag test accounts, and leave them out of the call totals

**Files:**
- Modify: `src/reef/models.py` (add `TestAccount`)
- Modify: `src/reef/rls.py` (add `test_account_statements()`; change `_CALL_LOG_TOTALS`)
- Create: `src/reef/piccolo_migrations/reef_2026_11_22t10_00_00_000000.py`
- Modify: `tests/test_migration_chain.py` (add `test_account_statements()` to the list at line 308)
- Test: `tests/test_test_accounts.py`

**Interfaces:**
- Produces: table `test_accounts (person_id uuid PRIMARY KEY REFERENCES persons ON DELETE CASCADE, flagged_at timestamptz NOT NULL DEFAULT now(), reset_at timestamptz NULL)`. Policy: a person may `SELECT` only their own row. No insert, update or delete policy.
- Produces: `reef_test_account_reset() RETURNS timestamptz`, definer. For the armed person only: raises `not a test account` (SQLSTATE `insufficient_privilege`) when unflagged; otherwise deletes their `jobs` with status `queued`, their `embedding_spend` and `reading_spend` rows, sets `reset_at = now()`, returns it.
- Produces: `reef_test_account_hurry() RETURNS integer`, definer. For the armed, flagged person: `UPDATE jobs SET run_after = now() WHERE person_id = <them> AND status = 'queued'`, returns the count; returns 0 when unflagged.
- Produces: `reef_call_log_totals(p_days)` unchanged in signature; skips rows whose `person_id` is in `test_accounts`.

- [ ] **Step 1: Write the failing tests** in `tests/test_test_accounts.py`, using the conftest helpers and the app role:
  - `test_person_cannot_flag_themselves`: as an armed person, `INSERT INTO test_accounts (person_id) VALUES (<own id>)` raises a row-security error.
  - `test_person_sees_only_own_flag`: two flagged people (flagged as the owner role); each, armed, selects exactly one row, their own.
  - `test_reset_function_refuses_unflagged`: armed unflagged person calls `reef_test_account_reset()`; raises; their queued job still exists.
  - `test_reset_function_clears_jobs_and_spend`: flagged person with one queued job, one running job, one `embedding_spend` and one `reading_spend` row; after the call the queued job and both spend rows are gone, the running job remains, `reset_at` is set.
  - `test_hurry_moves_queued_jobs`: a queued job with `run_after` an hour ahead; after `reef_test_account_hurry()` it is `<= now()`; returns 1.
  - `test_totals_skip_test_accounts`: call-log rows from yesterday for a flagged and an unflagged person; `reef_call_log_totals(7)` counts only the unflagged person's.

- [ ] **Step 2: Run them to see them fail**

Run: `just test-py tests/test_test_accounts.py`
Expected: FAIL, relation `test_accounts` does not exist.

- [ ] **Step 3: Implement** the model, `test_account_statements()` (table, RLS, policy, both functions via `_function_ddl`, `GRANT SELECT ON test_accounts TO reef_authz`), the new totals body, and the migration as fixed SQL printed from those statements, following `reef_2026_11_20t16_00_00_000000.py`. Add a sentence to `call_log_statements()`'s docstring: statistics across people skip flagged test accounts.

- [ ] **Step 4: Run the tests and the chain check**

Run: `just test-py tests/test_test_accounts.py tests/test_migration_chain.py tests/test_call_log.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/reef/models.py src/reef/rls.py src/reef/piccolo_migrations/reef_2026_11_22t10_00_00_000000.py tests/test_migration_chain.py tests/test_test_accounts.py
git commit -m "feat(test-accounts): flag made-up accounts and leave them out of call totals"
```

### Task 2: Confirmation tokens die at a reset

**Files:**
- Modify: `src/reef/methods/staged.py` (`stage`, `take`)
- Modify: `src/reef/methods/maintain.py:292-402` (`_take` and its callers)
- Modify: `src/reef/methods/share.py:135`
- Test: `tests/test_staged.py` (extend)

**Interfaces:**
- Consumes: `test_accounts.reset_at` (Task 1).
- Produces: `stage(...)` seals an `iat` (issue time, seconds) into the token. `async def take(caller: Caller, action: str, token: str, *, now: float | None = None) -> dict` now also raises `MethodError("stale_token")` when the caller has a `test_accounts` row whose `reset_at` is after the token's `iat`. Every caller awaits it.

- [ ] **Step 1: Write the failing tests**
  - `test_token_staged_before_reset_is_stale`: flagged person stages `forget`; set their `reset_at` to now (as owner); `await take(...)` raises `MethodError` with code `stale_token`.
  - `test_token_staged_after_reset_works`: `reset_at` an hour ago; stage then take returns the subject.
  - `test_unflagged_person_tokens_unaffected`: no `test_accounts` row; stage then take returns the subject.

- [ ] **Step 2: Run them to see them fail**

Run: `just test-py tests/test_staged.py`
Expected: FAIL (`take` is not awaitable, no `iat`).

- [ ] **Step 3: Implement** `iat` in `stage`, the async `take` with one read of the caller's own `test_accounts` row, and `await` at the three call sites.

- [ ] **Step 4: Run the tests and every method test**

Run: `just test-py tests/test_staged.py tests/test_method_maintain.py tests/test_method_share.py`
Expected: PASS.

- [ ] **Step 5: Commit** with message `feat(staged): a reset makes earlier confirmation tokens stale`.

### Task 3: The made-up person, and loading it

**Files:**
- Create: `src/reef/testaccount/__init__.py`, `src/reef/testaccount/fixture.py`
- Create: `src/reef/testaccount/person/person.toml`, `persona.md`, `engrams/**.md`, `files.toml`, `files/*`
- Create: `scripts/conversations/make_files.py` (generates the PDF and scan once; output committed)
- Test: `tests/test_testaccount_fixture.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class FixtureEngram: path: str; title: str; tags: list[str]; body: str`; `@dataclass(frozen=True) class FixtureFile: filename: str; mime: str; description: str; engram: str | None; data: bytes`; `@dataclass(frozen=True) class Fixture: version: int; persona: str; engrams: list[FixtureEngram]; files: list[FixtureFile]`.
- Produces: `def read_fixture() -> Fixture` (reads the package data with `importlib.resources`).
- Produces: `async def load_fixture(principal: Principal, fixture: Fixture, store: ObjectStore) -> tuple[int, int]` — saves the persona to `meta/persona.md` (`allow_protected=True`), every engram into `personal` with `save_engram(..., message="test account reset", channel="system", via=None)`, every file with `add_attachment`; returns (engrams, files).

Fixture rules: 30 to 40 engrams in Dutch and English (profile, household, work notes, how-tos, some linking with `[[...]]`), one training log with dated entries, a text PDF of 3 to 5 pages, a scanned page (an image of printed text, made with the PDF helper in `tests/filefixtures.py` or Pillow), one photo with a description. `person.toml` holds `version = 1` and the made-up name. Engram front matter is TOML between `+++` lines with `title` and `tags`.

- [ ] **Step 1: Write the failing tests**
  - `test_fixture_reads`: `read_fixture()` gives `version >= 1`, a non-empty persona, 30 to 40 engrams, at least one path under `logs/` or named as the training log, 3 files: a text PDF (`application/pdf`), a scan (`application/pdf` or `image/png`) and a photo (`image/jpeg`).
  - `test_fixture_paths_are_valid`: every engram path passes `reef.engrams.normalize_path` unchanged and `validate_body` accepts every body.
  - `test_fixture_is_shipped`: `importlib.resources.files("reef.testaccount") / "person" / "person.toml"` exists (package data is included).
  - `test_load_fixture_into_empty_person`: a fresh person; `load_fixture` returns (len engrams, len files); `list_engrams(principal, "personal")` holds exactly the fixture paths plus `meta/persona.md`; no `engram_access` rows were written.

- [ ] **Step 2: Run them to see them fail**

Run: `just test-py tests/test_testaccount_fixture.py`
Expected: FAIL, module `reef.testaccount` not found.

- [ ] **Step 3: Write the fixture content, `make_files.py`, and implement `read_fixture` and `load_fixture`.** Check that the build includes the data files (`pyproject.toml` package-data or hatch include); fix it if not.

- [ ] **Step 4: Run the tests**

Run: `just test-py tests/test_testaccount_fixture.py`
Expected: PASS.

- [ ] **Step 5: Commit** with message `feat(test-accounts): a made-up person to load into test accounts`.

### Task 4: The reset and readiness

**Files:**
- Modify: `src/reef/testaccount/__init__.py`
- Create: `src/reef/testaccount/reset.py`
- Test: `tests/test_testaccount_reset.py`

**Interfaces:**
- Consumes: Task 1's functions, Task 3's `read_fixture`, `load_fixture`.
- Produces: `class NotATestAccount(Exception)` and `class SharedWithOthers(Exception)`.
- Produces: `@dataclass(frozen=True) class ResetResult: fixture_version: int; engrams: int; files: int; services: dict[str, bool]` (`services` keys `embedding`, `rerank`, `file_text`: whether each is configured).
- Produces: `async def reset(principal: Principal, store: ObjectStore) -> ResetResult`. In one transaction: take `pg_advisory_xact_lock` keyed on the person; check the flag (own `test_accounts` row) and the membership rule; delete revisions, attachments (any status, keys collected), engrams and promotions in the person's spaces, as `account.delete_account_rows` orders it; call `reef_test_account_reset()`; `load_fixture`; call `reef_test_account_hurry()`. After commit, `erase_objects(keys)`; a failure there logs the count only.
- Produces: `@dataclass(frozen=True) class Readiness: ready: bool; vectors_missing: int; files_unread: int; jobs_waiting: int`; `async def readiness(principal: Principal) -> Readiness` (vectors from `embedding._lacking` and `files_lacking` when an embedder is configured; files with `text_status = 'waiting'` when reading is on; queued or running jobs of the embed and read kinds).

- [ ] **Step 1: Write the failing tests**
  - `test_reset_refuses_real_account`: unflagged person with two engrams; `reset` raises `NotATestAccount`; both engrams remain.
  - `test_reset_refuses_shared_space`: flagged person who owns a space with another member; raises `SharedWithOthers`; nothing deleted. Same when the flagged person is a member of someone else's space.
  - `test_reset_restores_fixture`: flagged person; save an extra engram, overwrite a fixture engram, store an extra file, leave a `pending` attachment and a queued job; `reset`; the personal space holds exactly the fixture (paths and bodies), revision count per engram is 1, the extra file and the pending row are gone, the shared space (sole member) is empty, `erase_objects` got the removed keys.
  - `test_reset_twice_concurrently`: two `reset` calls under `asyncio.gather` on separate connections; both succeed; exactly one copy of each fixture engram.
  - `test_reset_clears_usage`: open an engram (usage recorded) then reset; no `engram_access` or `engram_heat` rows remain for the person.
  - `test_readiness_after_reset`: with no embedder and reading off, `readiness` is `ready=True` with zeros; with a fake embedder configured and no vectors, `vectors_missing` equals the fixture's engram count and `ready=False`.

- [ ] **Step 2: Run them to see them fail**

Run: `just test-py tests/test_testaccount_reset.py`
Expected: FAIL, `reset` not defined.

- [ ] **Step 3: Implement `reset` and `readiness`.** If a delete the person needs is refused by a policy (check promotions), add it to `reef_test_account_reset()` in Task 1's statements and a new migration, not a broader policy.

- [ ] **Step 4: Run the tests**

Run: `just test-py tests/test_testaccount_reset.py tests/test_test_accounts.py`
Expected: PASS.

- [ ] **Step 5: Commit** with message `feat(test-accounts): reset a test account to the made-up person`.

### Task 5: The web routes

**Files:**
- Create: `src/reef/web/routes_test_account.py`
- Modify: `src/reef/server.py:244` (register next to `register_api_routes(mcp)`)
- Test: `tests/test_routes_test_account.py`

**Interfaces:**
- Consumes: Task 4's `reset`, `readiness`, `NotATestAccount`, `SharedWithOthers`.
- Produces: `async def principal_from_bearer(request: Request) -> Principal` — verifies `Authorization: Bearer <token>` with the server's auth provider (`mcp.auth.verify_token`) and resolves it with `reef.auth.principal_from_claims`; with no auth provider and `REEF_DEV_INSECURE=1`, uses `REEF_DEV_PRINCIPAL_EMAIL` as `current_principal` does; raises `AccessDenied` otherwise.
- Produces: `def register_test_account_routes(mcp: FastMCP) -> None` adding:
  - `POST /api/test-account/reset` → 200 `{"fixture_version", "engrams", "files", "services", "reef_version"}`; 401 `{"error": "unauthenticated"}`; 403 `{"error": "not_a_test_account"}` or `{"error": "shared_with_others"}`.
  - `GET /api/test-account/ready` → 200 `{"ready", "vectors_missing", "files_unread", "jobs_waiting"}`; same 401 and 403.

- [ ] **Step 1: Write the failing tests** with Starlette's `TestClient` and a stub auth provider:
  - `test_reset_without_token_is_401` and `test_reset_with_bad_token_is_401`.
  - `test_reset_for_unflagged_is_403`: valid token for an unflagged person; 403 `not_a_test_account`; their engrams remain.
  - `test_reset_for_flagged_is_200`: body has `fixture_version` equal to `read_fixture().version` and `reef_version` equal to the package version.
  - `test_ready_reports_counts`: 200 with the four keys.
  - `test_reset_is_not_an_mcp_tool`: the MCP tool list does not contain any name with `reset`.

- [ ] **Step 2: Run them to see them fail**

Run: `just test-py tests/test_routes_test_account.py`
Expected: FAIL, 404.

- [ ] **Step 3: Implement and register the routes.**

- [ ] **Step 4: Run the tests**

Run: `just test-py tests/test_routes_test_account.py`
Expected: PASS.

- [ ] **Step 5: Commit** with message `feat(test-accounts): reset and readiness routes for signed-in test accounts`.

### Task 6: `reef reset-test-account`

**Files:**
- Modify: `clients/python/src/reef_cli/cli.py`
- Test: `tests/test_cli.py` (extend; the CLI's tests live there)

**Interfaces:**
- Consumes: Task 5's routes.
- Produces: `reef reset-test-account [--timeout SECONDS]` (default 300). POSTs the reset to `<base>/api/test-account/reset`, where `<base>` is `--url` without its trailing `/mcp`, then polls `GET /api/test-account/ready` every 2 seconds until `ready` or the timeout. Auth is the same as for tools: `REEF_ACCESS_TOKEN` as a bearer when set, else the cached `OAuth` object used as `httpx` auth. Prints `{"reset": true, "fixture_version", "engrams", "files", "services", "reef_version", "seconds"}` and exits 0; on 401, 403 or timeout prints `{"error": ...}` and exits 1.

- [ ] **Step 1: Write the failing tests** with `httpx.MockTransport`:
  - `test_reset_polls_until_ready`: reset 200, ready false twice then true; exit 0; output has `seconds`.
  - `test_reset_refused`: reset 403 `not_a_test_account`; exit 1; output `{"error": "not_a_test_account"}`.
  - `test_reset_times_out`: ready never true with `--timeout 1`; exit 1; `{"error": "not_ready"}`.
  - `test_base_url_strips_mcp`: `--url https://example.test/mcp` posts to `https://example.test/api/test-account/reset`.

- [ ] **Step 2: Run them to see them fail**

Run: `just test-py tests/test_cli.py`
Expected: FAIL, unknown command.

- [ ] **Step 3: Implement the command.**

- [ ] **Step 4: Run the CLI's tests**

Run: `just test-py tests/test_cli.py`
Expected: PASS.

- [ ] **Step 5: Commit** with message `feat(cli): reset-test-account`.

### Task 7: The local test account

**Files:**
- Create: `scripts/conversations/local_account.py`
- Modify: `Justfile` (recipe `conversations-account`)
- Test: `tests/test_conversations_local_account.py`

**Interfaces:**
- Consumes: Task 4's `reset`; `scripts/eval/evalkit.refuse_unless_local`.
- Produces: `async def ensure_local_account(email: str = "conversations@reef.local", shared: str = "household") -> UUID` — refuses unless local; creates the person (as `seed.py` creates `test@reef.local`), their personal space and a shared space `household` with no other member, flags them in `test_accounts` as the owner role, then runs `reset` with `S3ObjectStore()` (the store `store_file` uses) and returns the person id. Running it twice is safe.
- Produces: `just conversations-account`.

- [ ] **Step 1: Write the failing tests**
  - `test_creates_and_flags`: against `reef_test`, returns an id; the person is flagged; `household` exists with one member; the personal space holds the fixture.
  - `test_twice_is_safe`: a second call returns the same id and still exactly one copy of the fixture.
  - `test_refuses_remote`: with `RAILWAY_ENVIRONMENT` set, raises `NotLocal`.

- [ ] **Step 2: Run them to see them fail**

Run: `just test-py tests/test_conversations_local_account.py`
Expected: FAIL.

- [ ] **Step 3: Implement the script and the recipe.**

- [ ] **Step 4: Run the tests, then the recipe against the local database**

Run: `just test-py tests/test_conversations_local_account.py && just conversations-account`
Expected: PASS, then one line saying the account is ready.

- [ ] **Step 5: Commit** with message `feat(conversations): a local made-up test account`.

### Task 8: Scenarios, the runner and scoring

**Files:**
- Create: `tests/conversations/scenarios.toml`
- Create: `scripts/conversations/events.py`, `scripts/conversations/score.py`, `scripts/conversations/run.py`
- Create: `tests/conversations/sample-events.jsonl` (recorded in Step 1, made-up content only)
- Test: `tests/test_conversations_scoring.py`

**Interfaces:**
- Produces, `events.py`: `@dataclass(frozen=True) class ToolCall: server: str; tool: str; arguments: dict; ok: bool`; `@dataclass(frozen=True) class Session: session_id: str; tool_calls: list[ToolCall]; other_tools: list[str]; answer: str` (the last agent message); `def parse_events(lines: Iterable[str]) -> Session`.
- Produces, `score.py`: `Verdict = Literal["pass", "fail", "review", "skipped", "invalid"]`; `@dataclass(frozen=True) class Scored: id: str; verdict: Verdict; reasons: list[str]; tools: list[str]; seconds: float`; `def score(scenario: dict, session: Session | None, after: dict[str, str | None], *, file_text: bool, seconds: float) -> Scored`; `def coverage(scenarios: list[dict]) -> set[str]` (the tools named in `tools_required`).
- Produces, `run.py`: `python scripts/conversations/run.py --target local|production [--only ID ...] [--file-text on|off]`. It resets (local: in-process `reset` then waits on `readiness`; production: `uv run --project clients/python reef reset-test-account`), plays read scenarios in file order, then for each write scenario resets, plays it and reads every `after` ref (local in-process, production with `reef open`), resets once more so the account rests as the made-up person (also after a failure), and writes `~/.reef-conversations/reports/<UTC time>-<target>.json` and `.md`.
- Each message is sent with `codex exec --json -m gpt-6-astra -s read-only --skip-git-repo-check -C <a new empty temp dir>` plus the Reef server as `-c` settings; later messages use `codex exec resume <session_id>`. Local server: `mcp_servers.reef_local` running `python -m reef.server` over stdio with `REEF_DEV_PRINCIPAL_EMAIL=conversations@reef.local`. Production: `mcp_servers.reef_test.url = "https://reefwith.me/mcp"`. Scenarios with `connector = true` add `enabled_tools = ["search", "fetch"]`.

Scenario fields (TOML `[[scenario]]`): `id`, `kind` (`read` or `write`), `messages` (list; a confirmation is written as the person's next message), `tools_required`, `tools_forbidden`, `facts`, `engrams`, `needs` (list, may hold `"file_text"`), `connector` (bool), and for writes `[[scenario.after]]` with `ref`, `contains`, `absent`. Messages are in Dutch or English, the way a person types. 25 to 35 scenarios.

Scoring rules, in order: no session or a Codex error → `invalid`; any tool outside Reef's server (`other_tools`) → `invalid`; `needs` holds `file_text` and `file_text` is off → `skipped`; a required tool missing, a forbidden tool used, or an `after` check failing → `fail`; every fact found (case-insensitive, white space collapsed) and every engram named → `pass`; otherwise → `review`. Reports drop anything matching a staged token or a signed URL.

- [ ] **Step 1: Record a real sample.** Start `just conversations-account`, run one `codex exec --json` against `reef_local` with "What do you know about my training?", save its stdout as `tests/conversations/sample-events.jsonl`. Check it holds only made-up content.

- [ ] **Step 2: Write the failing tests**
  - `test_parse_sample`: `parse_events` on the sample gives a session id, at least one `ToolCall` with `server == "reef_local"`, and a non-empty `answer`.
  - `test_shell_use_is_invalid`: a session with `other_tools == ["shell"]` scores `invalid` even when every fact is present.
  - `test_missing_required_tool_fails`, `test_forbidden_tool_fails`, `test_after_check_fails`.
  - `test_file_text_skipped`: `needs = ["file_text"]`, `file_text=False` → `skipped`.
  - `test_fact_phrased_otherwise_is_review`: tools right, one fact absent → `review`.
  - `test_all_tools_covered`: `coverage(load("tests/conversations/scenarios.toml"))` equals the 16 tool names in `src/reef/server.py`.
  - `test_report_drops_tokens`: a scored answer containing a staged token and a signed URL is written without either.

- [ ] **Step 3: Run them to see them fail**

Run: `just test-py tests/test_conversations_scoring.py`
Expected: FAIL.

- [ ] **Step 4: Write the scenarios and implement `events.py`, `score.py` and `run.py`.**

- [ ] **Step 5: Run the tests, then one local run**

Run: `just test-py tests/test_conversations_scoring.py && python scripts/conversations/run.py --target local --only <one read id> <one write id>`
Expected: PASS, then a report naming both scenarios with a verdict each.

- [ ] **Step 6: Commit** with message `feat(conversations): scenarios, a Codex runner and scoring`.

### Task 9: Running it through Orca, and the production setup

**Files:**
- Create: `scripts/conversations/README.md`
- Create: `scripts/conversations/worker-task.md` (the task spec template for the Astra worker)
- Modify: `docs/runbook.md` (a "Test conversations" section)
- Create: `changes/test-conversations.md` only if the PR shows something a person using Reef would notice; otherwise label the PR `no-changelog`.

**Interfaces:**
- Consumes: everything above.
- Produces: the controller's command: `orca orchestration worker-start --spec-file scripts/conversations/worker-task.md --worktree new-child --name conversations-<target> --agent codex --model gpt-6-astra`, with the target filled in. The worker runs `run.py`, reports with `worker_done` the report paths and the count per verdict, and changes nothing else.
- Produces: the runbook's one-time production setup, in order: (1) Wouter creates the account with an address of his choice and a shared space with no other member; (2) Wouter signs in `codex mcp login reef_test -c 'mcp_servers.reef_test.url="https://reefwith.me/mcp"'` and `uv run --project clients/python reef login`, both as that account; (3) with Wouter's say-so, an operator runs `INSERT INTO test_accounts (person_id) SELECT id FROM persons WHERE email = '<address>'` as the owner role; (4) `reef reset-test-account`.

- [ ] **Step 1: Check that Codex keeps a login for a server defined only by `-c`.** Locally, define a throwaway HTTP server by `-c`, run `codex mcp list -c ...` and confirm it is listed with its auth state. If Codex cannot keep the login that way, write the fallback in the runbook (a `[mcp_servers.reef_test]` entry with `enabled = false` in `~/.codex/config.toml`, switched on per run with `-c mcp_servers.reef_test.enabled=true`) and flag it for Wouter, since it changes his Codex settings.

- [ ] **Step 2: Write the README, the worker task template and the runbook section.**

- [ ] **Step 3: Run a full local conversation run through Orca** with the command above and `--target local`.
Expected: the worker reports a report path; every scenario has a verdict; none is `invalid` because of the harness.

- [ ] **Step 4: Commit** with message `docs(conversations): run test conversations through Orca, and set up production`.
