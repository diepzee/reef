# Reef tools built around workflows: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Reef's 31 MCP tools with 16 workflow methods on a shared method layer, log a `reason` for every call, and rename cove to space and page to engram everywhere, as one release.

**Architecture:** First rename everything (database, code, web app, site), keeping today's tools working under new internals. Then add a transport-independent method layer (`src/reef/methods/`) whose wrapper logs every call, build the 16 methods on it, and swap the MCP registrations in `server.py` for one-line shells. The operating protocol is rewritten last, against the final tool names.

**Tech Stack:** Python 3.13, FastMCP 3.4.6, Piccolo ORM, Postgres 18 with forced row security, pydantic-settings, React web app in `frontend/`, pytest via `just test-py`.

**Spec:** `docs/specs/2026-11-19-workflow-tool-set-design.md`

## Global Constraints

- One release: everything here merges as one pull request, on a quiet `main`. Merging to `main` deploys to production and needs Wouter's explicit approval.
- Rename rule, in the domain sense only: cove → space, coves → spaces, Cove → Space, page → engram, pages → engrams, Page → Engram, `cove_id` → `space_id`, `page_id` → `engram_id`. A web page, the privacy page, pagination and "the whole-page piece" in the sense of a web document keep the word page; `PAGE_CHARS`-style names for the whole-engram piece become engram.
- The 16 method names, exactly: `load_index`, `recall`, `brief`, `open`, `catch_up`, `remember`, `log`, `write`, `store_file`, `correct`, `undo`, `forget`, `share`, `instruct`, `search`, `fetch`.
- `reason`: every method except `search` and `fetch` takes `reason: str`, required in the signature, empty accepted, cut to 200 characters. `search` and `fetch` take no reason.
- Call log: off unless `REEF_CALL_LOG` is set; rows private to the person under row security; deleted after 90 days (a constant, not a setting); purged hourly by the worker; included in the export; `reason` never reaches Logfire or the app logs.
- Writer kind values: `assistant` (MCP), `person` (website), `system` (reserved, unused here).
- Staged actions (`share`, `forget`, `instruct`): two calls; the first returns `{"effect": str, "token": str, "expires_in": 600}`; only a second call with that token acts. 10-minute lifetime.
- `instruct` writes the persona in the personal space only.
- `share` moves, as today: the private original becomes a pointer.
- Logs in parts: when an entry would take a log past `get_settings().engrams.max_chars`, `log` starts `<stem>-2.md`, `-3`, …; each part is tagged `log` and links to the part before it; the part before gets a closing link to the new part; no text moves.
- Old tool names are not listed. A call to one returns an error naming the method that replaces it.
- Environment variables: new names where they say page (`REEF_ENGRAM_MAX_CHARS`, `REEF_RERANK_MIN_ENGRAMS`); the old names are still read.
- Export: `schema_version` 2, with `spaces` and `engrams`.
- Docstrings in ReST, no types in them. Never send page text, queries or reasons to a third party.
- Merges use `gh pr merge --squash` without `--delete-branch` when the branch is checked out in a worktree.

## Review Focus

1. A staged token used by another person, for another action, after expiry, or twice: refused, with nothing done. Test in Task 9 and Task 10.
2. Two `log` calls on the same engram at once: both entries land, in order. Test in Task 8.
3. A log entry that alone exceeds the size cap: refused with a clear message, no empty part created. Test in Task 8.
4. A `reason` holding sensitive text: absent from the app log and from telemetry attributes, also when the method fails. Test in Task 5.
5. `forget`, `open` or `undo` on an engram or file in a space the caller cannot read: the same not-found answer as for one that does not exist. Test in Task 6 and Task 9.

---

### Task 1: Rename the backend and the database

**Files:**
- Create: `src/reef/piccolo_migrations/reef_2026_11_20t10_00_00_000000.py`
- Rename: `src/reef/pages.py` → `engrams.py`, `src/reef/coves.py` → `spaces.py`, and every other module, class, function, constant and SQL string under `src/reef/` and `scripts/` per the rename rule
- Modify: `src/reef/models.py`, `src/reef/rls.py`, `src/reef/search.py`, `src/reef/embedding.py`, `src/reef/usage.py`, `src/reef/searchlog.py`, `src/reef/export.py`, `scripts/eval/*.py`
- Test: every file under `tests/`, renamed alongside (`tests/test_pages.py` → `test_engrams.py`, …)

**Interfaces:**
- Produces: tables `spaces`, `engrams`, `engram_embeddings`, `engram_access`, `engram_heat`, `space_appearances`; columns `space_id`, `engram_id`, `dest_space_id`, `source_engram_id`, `engram_ids`; models `Space`, `Engram`, `EngramEmbedding`; `reef.search.search_engrams` (was `search_pages`); `reef.engrams.save_engram`, `get_engram`, `get_engram_as_of`, `edit_section`, `delete_engram`, `list_engrams`; `reef.spaces.*`; `reef.access.resolve_space`, `resolve_writable_space`, `accessible_spaces`, `alias_map`; security functions `reef_member_space_ids()` and the other four renamed.
- MCP tool names stay as they are in this task.

- [ ] **Step 1: Generate the database rename from the catalog.** Write `scripts/rename_sql.py` (kept in the repo) that, against the local database, lists every table, column, index, constraint and policy whose name matches `cove|page` in the domain sense and prints `ALTER … RENAME …` statements. Paste its output into the migration as static SQL. After the renames, the migration re-runs the security DDL from `reef.rls` (functions, policies, grants) so function bodies name the new tables. Expect 6 tables, 14 columns, 5 functions, 17 policies, 10 indexes, as measured on production on 19 November 2026.
- [ ] **Step 2: Rename the Python code** with a scripted pass plus review, following the rename rule. Run `grep -rniE "\bcoves?\b|cove_|Cove" src scripts` and expect no matches except in the migration's old names. For `page`, review each remaining match by hand against the rule.
- [ ] **Step 3: Rename the tests** the same way, and update `tests/conftest.py` fixtures (`w_personal` stays, `household` stays; `graph.personal_cove` → `personal_space`, `shared_cove` → `shared_space`).
- [ ] **Step 4: Run the migration chain test and the full suite.** Run: `just test-py`. Expected: all pass, including `tests/test_migration_chain.py` (models and migrations agree).
- [ ] **Step 5: Rehearse the migration** on a local database restored from production's schema (`pg_dump --schema-only`) plus the test fixtures' data: apply all migrations up to `main`, then this one, then run `just test-py tests/test_rls.py`. Expected: pass. Never copy production's data.
- [ ] **Step 6: Commit.** `git commit -m "refactor: rename cove to space and page to engram in the backend and database (RUG-40)"`

### Task 2: Rename the web API and the web app

**Files:**
- Modify: `src/reef/web/routes_api.py`, `src/reef/web/*.py`, every file under `frontend/src/` that names coves or pages in the domain sense
- Test: `tests/test_web_api_*.py`, `frontend/src/**/*.test.*`

**Interfaces:**
- Consumes: Task 1's renamed modules.
- Produces: API paths `/api/spaces`, `/api/spaces/{space}`, `/api/spaces/{space}/appearance|invites|leave|members|name`, `/api/engrams/{space}/{path:path}`, `/api/files/{space}/{key:path}`, `/api/images/{space}/{key:path}`. App routes use `space` and `engram`; old app routes redirect to the new ones.

- [ ] **Step 1: Write the failing test** `test_old_app_paths_redirect` in `tests/test_web_static.py`: a GET on today's app path for a cove page returns a redirect to the new space/engram path.
- [ ] **Step 2: Rename the API routes and their handlers;** update the web app's API client and routes in the same commit; add the redirect.
- [ ] **Step 3: Rename user-facing words in the web app:** "cove" → "space", "page" → "engram" where it means a stored note.
- [ ] **Step 4: Run** `just test` (lint, Python tests and the web app's tests). Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "refactor: rename cove to space and page to engram in the web app (RUG-40)"`

### Task 3: Rename the site, settings, export and docs

**Files:**
- Modify: `site/*.html`, `src/reef/config.py`, `src/reef/export.py`, `docs/runbook.md`, `scripts/eval/README.md`, `scripts/eval/evalkit.py`
- Create: `docs/glossary.md` (copy the main checkout's untracked version, which already says space)
- Test: `tests/test_settings.py`, `tests/test_export*.py`, `tests/test_config_prefix.py`

**Interfaces:**
- Produces: settings `get_settings().engrams.max_chars` (group renamed from `pages`), `get_settings().rerank.min_engrams`; variables `REEF_ENGRAM_MAX_CHARS` and `REEF_RERANK_MIN_ENGRAMS`, with `REEF_PAGE_MAX_CHARS` and `REEF_RERANK_MIN_PAGES` still read; export `schema_version` 2 with keys `spaces` and `engrams`.

- [ ] **Step 1: Write the failing tests:** `test_old_engram_cap_name_still_read` (`REEF_PAGE_MAX_CHARS=7000` → `engrams.max_chars == 7000`), `test_new_name_wins_over_old` (both set → new one), and `test_export_schema_2` (`schema_version == 2`, keys `spaces` and `engrams`).
- [ ] **Step 2: Implement** with `validation_alias=AliasChoices(new, old)`; teach `reef.config.variables()` to report the first choice and the test pinning names to accept both.
- [ ] **Step 3: Update `evalkit.private_pages`** to read schema 2 and still read schema 1, since the private export on disk is schema 1.
- [ ] **Step 4: Rename the words on the site,** including the privacy page; run `just settings-table`.
- [ ] **Step 5: Run** `just test`. Expected: pass.
- [ ] **Step 6: Commit.** `git commit -m "refactor: rename cove and page in the site, settings, export and docs (RUG-40)"`

### Task 4: The call log and the writer kind in history

**Files:**
- Create: `src/reef/calllog.py`, `src/reef/piccolo_migrations/reef_2026_11_20t12_00_00_000000.py`
- Modify: `src/reef/models.py` (`CallLog`; `Revision.channel`), `src/reef/rls.py` (`call_log_statements()`, `CALL_LOG_DAYS = 90`, `CALL_LOG_MAX_REASON = 200`), `src/reef/config.py` (group `CallSettings`, prefix `REEF_CALL_`, field `log: bool = False`, mounted as `Settings.calls`), `src/reef/worker.py` (purge with the search log's), `src/reef/export.py` (`data/calls.json`), `src/reef/telemetry.py` (`"reason"` in `SCRUB_PATTERNS`), `src/reef/engrams.py` (`save_engram(..., channel: str = "assistant")`), `src/reef/web/routes_api.py` (pass `channel="person"`), `site/privacy.html` (a row for the call log, "not switched on yet")
- Test: `tests/test_calllog.py`

**Interfaces:**
- Produces: `async def record(principal: Principal, *, session_id: str | None, writer: str, method: str, reason: str, duration_ms: int, returned: int, outcome: str) -> None` (no-op unless `get_settings().calls.log`; insert in a savepoint; logs only the error class); `async def purge() -> int`; table `call_log(id, person_id, session_id, method, reason, writer, duration_ms, returned, outcome, called_at)`; column `revisions.channel` nullable text.

- [ ] **Step 1: Write the failing tests** in `tests/test_calllog.py`, following `tests/test_searchlog.py`: off by default records nothing; on records one row with the reason cut to 200 characters; a person cannot see another's rows; rows older than 90 days are purged; the export contains the person's calls; a failed insert does not fail the caller.
- [ ] **Step 2: Implement** the model, policies (self-only select and insert, no update), purge function owned by `reef_authz`, migration, setting and module.
- [ ] **Step 3: Write the failing test** `test_website_writes_record_person` and `test_assistant_writes_record_assistant` on `revisions.channel`; implement.
- [ ] **Step 4: Run** `just test-py tests/test_calllog.py tests/test_migration_chain.py tests/test_export*.py`. Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "feat(calls): a private call log with reasons, off until switched on (RUG-40)"`

### Task 5: The method layer

**Files:**
- Create: `src/reef/methods/__init__.py` (`Caller`, `method`, `REGISTRY`, `MethodError`), `src/reef/methods/staged.py`
- Test: `tests/test_methods_layer.py`

**Interfaces:**
- Consumes: Task 4's `calllog.record`.
- Produces:
  - `@dataclass(frozen=True) class Caller: principal: Principal; writer: Literal["assistant", "person", "system"]; session_id: str | None`
  - `def method(name: str) -> Callable` — decorator for `async def fn(caller: Caller, reason: str, **kwargs) -> dict | list`. It cuts `reason` to 200 characters, times the call, records it with `calllog.record(caller.principal, session_id=caller.session_id, writer=caller.writer, …)` whether it succeeds or raises (outcome `ok` or the error class), and re-raises. Registers `fn` in `REGISTRY[name]`.
  - `class MethodError(Exception)` with `code: str` and a plain `message` for the assistant.
  - `staged.stage(caller: Caller, action: str, subject: dict) -> dict` returning `{"token", "expires_in": 600}`; `staged.take(caller: Caller, action: str, token: str) -> dict` returning `subject` or raising `MethodError("stale_token")`. Tokens are sealed with the existing `seal`/`unseal` helpers and `get_settings().web.session_secret`, bound to person id, action and expiry.

- [ ] **Step 1: Write the failing tests:** `test_reason_is_cut_to_200`, `test_empty_reason_is_accepted_and_counted` (outcome row has `reason == ""`), `test_failed_method_is_logged_and_reraised`, `test_reason_never_reaches_the_app_log` (caplog with a reason "diagnosis XYZ" on a failing method: "XYZ" absent), `test_token_refused_for_other_person`, `test_token_refused_for_other_action`, `test_token_refused_after_expiry` (freeze time + 601 s).
- [ ] **Step 2: Implement** `methods/__init__.py` and `methods/staged.py`.
- [ ] **Step 3: Run** `just test-py tests/test_methods_layer.py`. Expected: pass.
- [ ] **Step 4: Commit.** `git commit -m "feat(methods): a shared method layer that logs every call (RUG-40)"`

### Task 6: Find: `load_index`, `recall`, `open`, `catch_up`

**Files:**
- Create: `src/reef/methods/find.py`
- Modify: `scripts/eval/evalkit.py` (mode `recall`)
- Test: `tests/test_method_find.py`

**Interfaces:**
- Consumes: `context.build_index`, `protocol.build_instructions`, `search.search_engrams` (Task 1's name for `search_pages`), `sections.split`, `context.latest_editors`, `engrams.get_engram`, `engrams.get_engram_as_of`, `attachments.get_attachment`, `activity.whats_new`.
- Produces:
  - `load_index(caller, reason) -> dict`: today's index payload plus, per space, its members (from `list_coves`), with the protocol and persona.
  - `recall(caller, reason, question: str, space: str | None = None, limit: int = 5) -> list[dict]`: each `{"space", "engram", "title", "passage", "author", "updated_at", "ref"}`; `passage` is the best-matching piece of the engram (as the reranker reads it), at most 1,500 characters; `ref` is `"<space>/<path>"`. Ranking is `search_engrams(..., log=True)`. When there are no vectors, `passage` is the search snippet.
  - `open(caller, reason, refs: list[str], as_of: str | None = None) -> list[dict]`: a ref `"<space>/<path>"` gives the whole engram; `"<space>/file/<key>"` gives a file's metadata and short-lived URL. Unknown or unreadable refs come back as `{"ref", "error": "not_found"}`.
  - `catch_up(caller, reason, since: str | None = None) -> dict`: today's `whats_new`.

- [ ] **Step 1: Write the failing tests:** `test_recall_returns_passage_and_source`, `test_recall_passage_is_the_section_that_answers` (an engram with a `## Car` and a `## House` section; a car question returns the car section), `test_recall_respects_space`, `test_open_engram_and_file`, `test_open_unreadable_is_not_found` (partner's personal engram: identical answer to a missing one), `test_load_index_lists_members_per_space`.
- [ ] **Step 2: Implement** `methods/find.py`.
- [ ] **Step 3: Add the eval mode `recall`** and run it on the private set locally: `REEF_EMBEDDING_URL=http://127.0.0.1:11434 .venv/bin/python scripts/eval/run.py --private --mode recall --mode hybrid`. Expected: found in the first five at least as often as mode `hybrid`.
- [ ] **Step 4: Run** `just test-py tests/test_method_find.py`. Expected: pass.
- [ ] **Step 5: Commit.** `git commit -m "feat(methods): recall, open, catch up and the index (RUG-40)"`

### Task 7: Find: `brief`

**Files:**
- Create: `src/reef/methods/logs.py` (log series helpers, shared with Task 8)
- Modify: `src/reef/methods/find.py`
- Test: `tests/test_method_brief.py`

**Interfaces:**
- Consumes: Task 6's ranking.
- Produces:
  - `logs.series(caller, space: str, path: str) -> list[Engram]`: the parts of one log, first to last; parts share the stem (`climbing-log.md`, `climbing-log-2.md`, …), sit in the same folder and carry the tag `log`.
  - `logs.last_entries(parts: list[Engram], n: int) -> list[str]`: the last `n` entries across parts; an entry is a section at the deepest heading level the log uses for entries (level 3 when present, else 2).
  - `brief(caller, reason, topic: str, space: str | None = None, entries: int = 10) -> dict`: `{"engrams": [{"space", "engram", "title", "body" | "entries", "parts"}]}`. Up to 5 related engrams from the ranking; a log gives its last `entries` entries across its parts, another engram its body cut to 8,000 characters; at most 30,000 characters in all.

- [ ] **Step 1: Write the failing tests:** `test_brief_gives_plan_and_recent_entries`, `test_brief_stitches_entries_across_parts` (a log in two parts, `entries=3`, the last part holding one entry: two from part 1, one from part 2, in order), `test_brief_stays_within_budget`, `test_brief_without_matches_is_empty`.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run** `just test-py tests/test_method_brief.py`. Expected: pass.
- [ ] **Step 4: Commit.** `git commit -m "feat(methods): brief, with logs read across their parts (RUG-40)"`

### Task 8: Capture: `remember`, `log`, `write`, `store_file`

**Files:**
- Create: `src/reef/methods/capture.py`, `src/reef/piccolo_migrations/reef_2026_11_20t14_00_00_000000.py` (tags existing `*-log.md` engrams with `log`)
- Modify: `src/reef/methods/logs.py`
- Test: `tests/test_method_capture.py`

**Interfaces:**
- Consumes: `engrams.save_engram`, `server.tool_remember`'s logic (moved into the method), `attachments.add_attachment`, Task 7's `logs.series`.
- Produces:
  - `remember(caller, reason, fact: str, space: str = "personal") -> dict`
  - `log(caller, reason, space: str, engram: str, heading: str, entry: str) -> dict`: `{"engram": <path written>, "part": int, "version": int}`. Appends `"\n\n### {heading}\n\n{entry}\n"` at the end of the last part, under a row lock; creates the engram tagged `log` when missing; starts the next part when the entry would exceed `get_settings().engrams.max_chars`; refuses with `MethodError("entry_too_long")` when the entry alone would.
  - `write(caller, reason, space: str, engrams: list[dict], message: str) -> list[dict]`: as `write_pages` today; refuses the persona path with `MethodError("use_instruct")`.
  - `store_file(caller, reason, space: str, filename: str, data_base64: str, mime: str, description: str, engram: str | None = None) -> dict`

- [ ] **Step 1: Write the failing tests:** `test_log_appends_and_touches_nothing_else` (the body before the new entry is byte-identical), `test_log_creates_a_tagged_log`, `test_log_starts_part_two_at_the_cap` (cap lowered by monkeypatch; part 2 exists, tagged, links back; part 1 gains a closing link), `test_log_refuses_an_entry_bigger_than_the_cap` (no part created), `test_two_logs_at_once_both_land` (two concurrent calls with separate transactions: both entries present), `test_write_refuses_the_persona`, `test_store_file_then_open`.
- [ ] **Step 2: Implement** `methods/capture.py` and the migration. Verify in the Task 1 rehearsal database that the migration role can update tags under forced row security; if it cannot, run the tagging inside the security-definer pattern used in `reef.rls`.
- [ ] **Step 3: Run** `just test-py tests/test_method_capture.py tests/test_migration_chain.py`. Expected: pass.
- [ ] **Step 4: Commit.** `git commit -m "feat(methods): remember, log in parts, write and store a file (RUG-40)"`

### Task 9: Maintain: `correct`, `undo`, `forget`

**Files:**
- Create: `src/reef/methods/maintain.py`
- Test: `tests/test_method_maintain.py`

**Interfaces:**
- Consumes: `engrams.edit_section`, revisions, `engrams.delete_engram`, `attachments.delete_attachment`, Task 5's `staged`.
- Produces:
  - `correct(caller, reason, space: str, engram: str, old_text: str, new_text: str, message: str, expected_version: int | None = None) -> dict`
  - `undo(caller, reason, space: str, engram: str, version: int | None = None) -> dict`: restores body, title and tags of version `version`, or of the version before the current one, as a new change with message `"Undo to version N"`; `MethodError("nothing_to_undo")` at version 1. Versions number the engram's revisions in order, starting at 1.
  - `forget(caller, reason, space: str, engram: str | None = None, file: str | None = None, confirm: str | None = None) -> dict`: without `confirm`, `{"effect", "token", "expires_in"}`; with a valid token, deletes and returns `{"forgotten": ref}`.

- [ ] **Step 1: Write the failing tests:** `test_correct_replaces_one_passage`, `test_undo_restores_the_previous_version_as_a_new_change`, `test_undo_to_a_given_version`, `test_undo_at_version_one_refuses`, `test_forget_needs_the_token`, `test_forget_token_cannot_delete_something_else` (token for engram A used with engram B: refused), `test_forget_unreadable_is_not_found`.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run** `just test-py tests/test_method_maintain.py`. Expected: pass.
- [ ] **Step 4: Commit.** `git commit -m "feat(methods): correct, undo and forget (RUG-40)"`

### Task 10: Share and instruct

**Files:**
- Create: `src/reef/methods/share.py`
- Test: `tests/test_method_share.py`

**Interfaces:**
- Consumes: `promotion.prepare_promotion`, `promotion.confirm_promotion`, the persona write in `server.tool_update_meta_page`, Task 5's `staged`.
- Produces:
  - `share(caller, reason, engram: str, to_space: str, section: str | None = None, to_path: str | None = None, confirm: str | None = None) -> dict`: the first call wraps `prepare_promotion` and returns `{"effect", "token": <its nonce>, "expires_in": 600}`; the second wraps `confirm_promotion`.
  - `instruct(caller, reason, body: str, confirm: str | None = None) -> dict`: the first call returns the effect and a token bound to a digest of `body`; the second, with the same body, writes the persona; a different body refuses.

- [ ] **Step 1: Write the failing tests:** `test_share_moves_and_leaves_a_pointer`, `test_share_token_refused_twice`, `test_instruct_needs_the_token`, `test_instruct_token_refused_for_a_different_body`, `test_instruct_writes_only_the_personal_persona`.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run** `just test-py tests/test_method_share.py`. Expected: pass.
- [ ] **Step 4: Commit.** `git commit -m "feat(methods): share and instruct in two steps (RUG-40)"`

### Task 11: Swap the MCP tools

**Files:**
- Modify: `src/reef/server.py` (remove the 31 registrations and the `tool_*` functions now living in methods; register the 16)
- Test: `tests/test_tool_surface.py` (new), existing tool tests moved to the method tests or deleted where a method test covers them

**Interfaces:**
- Consumes: `REGISTRY` from Tasks 5 to 10.
- Produces: exactly 16 listed tools with the names in Global Constraints; each MCP shell resolves the principal in a short transaction, builds `Caller(principal, "assistant", current_session_id())`, and calls the method outside it; `search` and `fetch` keep today's result shapes.

- [ ] **Step 1: Write the failing tests** in `tests/test_tool_surface.py`: `test_exactly_sixteen_tools` (names equal the set in Global Constraints), `test_reason_is_required_except_search_and_fetch` (from each tool's input schema), `test_annotations` (`load_index`, `recall`, `brief`, `open`, `catch_up`, `search`, `fetch` read-only; `forget` destructive), `test_old_tool_name_names_its_replacement` (calling `read_pages` returns an error containing "open"), `test_search_and_fetch_shapes_unchanged`.
- [ ] **Step 2: Implement.** Check FastMCP's app-only visibility for keeping old names callable but unlisted with an ordinary client; if it does not work end to end, use an unknown-tool handler that maps old names to the error message.
- [ ] **Step 3: Run** `just test`. Expected: pass.
- [ ] **Step 4: Commit.** `git commit -m "feat(server): sixteen workflow tools instead of thirty-one (RUG-40)"`

### Task 12: The operating protocol

**Files:**
- Modify: `src/reef/protocol.md`, `src/reef/protocol.py` (persona stub wording), tool docstrings in `src/reef/server.py`
- Test: `tests/test_protocol.py`

- [ ] **Step 1: Write the failing test** `test_protocol_names_only_existing_tools`: every backticked tool name in `protocol.md` is one of the 16.
- [ ] **Step 2: Rewrite `protocol.md`** for the 16 methods and the words space and engram: start with `load_index`; answer with `recall`; `brief` for advice and preparation; `open` only when passages are not enough; log sessions with `log`, never by rewriting; show the effect of `share`, `forget` and `instruct` and wait for a yes; give a real reason with every call, and say that it is logged privately.
- [ ] **Step 3: Run** `just test-py tests/test_protocol.py`. Expected: pass.
- [ ] **Step 4: Commit.** `git commit -m "docs(protocol): the operating protocol for the workflow tools (RUG-40)"`

### Task 13: Verify, document and open the pull request

**Files:**
- Modify: `docs/runbook.md` (the tool set, the call log, the rename's deploy note), `changes/rug-40.md`
- Create: a Linear ticket for switching the call log on

- [ ] **Step 1: Run** `just test`. Expected: all pass.
- [ ] **Step 2: Run each workflow once through Claude Code against a local Reef:** look up a fact, get a brief, log an entry, correct a passage, undo it, share and forget (both confirmed), instruct. Expected: each picks the intended tool; record the transcript summary in the pull request.
- [ ] **Step 3: Repeat the Task 1 rehearsal** with all migrations of this branch. Expected: pass.
- [ ] **Step 4: Write `changes/rug-40.md`** (kind `changed`) for people: the new words, and that assistants now have fewer, clearer tools.
- [ ] **Step 5: Create the switch-on ticket** for the call log under RUG-40: privacy page, telling Nathalie and the third account, then `REEF_CALL_LOG=1` on `reef-app`.
- [ ] **Step 6: Open the pull request,** announcing the breaking change and the short interruption during the deploy. Merging waits for Wouter.
- [ ] **Step 7: After the deploy,** measure `recall` read-only in production on the 40 private questions, as on 19 November 2026, inside `reef-app` without logging. Expected: at least 95% found in the first five. Ask Wouter to try a ChatGPT-style connector if he uses one.
