# Typed links and replaced engrams: implementation plan

> **For agentic workers:** build this task by task, in order. Each task ends
> with its tests passing and one commit per step group. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** an engram can say it replaces, contradicts, is about or is part of
another one, and a replaced engram ranks lower in search and names its
replacement.

**Architecture:** a pure parser takes the four keys' lines out of a leading
front matter block. Two callers use it: the `write` tool, which asks twice
before it adds a `replaces` link, and the web app's save route, which
writes at once. `save_engram` stores the relations as rows in the existing
`links` table. Search asks once per query which hits are replaced for this
reader and moves them after the others.

**Tech stack:** Python 3.13, Piccolo, Postgres 18 with row-level security,
FastMCP. No new dependency.

**Spec:** `docs/specs/2026-11-26-typed-links-design.md`. Read it first; this
plan argues from it, and the spec wins where they differ.

## Global constraints

- The stored body never holds the lines of `replaces`, `contradicts`,
  `about` or `part_of`. Every other line of a block stays, byte for byte.
- A body without such a block, or whose block holds none of the four keys,
  is stored exactly as today.
- Only two callers read a block: the `write` tool and the web app's save
  route. `correct`, `remember`, `log`, `undo`, `share` and the importer
  pass bodies through untouched.
- `replaces` is the written word; the stored kind is `supersedes`. No new
  kind, table or column.
- A link exists for a reader only when they can read both ends. No note,
  rank or count may tell a reader about an engram they cannot read.
- No link from a shared space into a personal one; none to itself; no
  circle of `replaces`; at most 20 links per key.
- A typed link's target must exist and be readable by the writer.
  `replaces` also needs write access to the target's space, enforced in
  the database.
- A save changes only the typed links the saver can read.
- No new tool. `write` gains one optional parameter, `confirm`.
- The first call of an assistant's `write` that would add a `replaces`
  link writes nothing, for no engram of that call, and returns
  `{"effect", "token", "expires_in"}` as `share` and `forget` do.
- Tool descriptions, message texts and the protocol are read by
  assistants: plain words, in the style of `src/reef/methods/__init__.py`
  and `src/reef/protocol.md`. `tests/test_protocol.py` guards the words.
- Python: modern types, a ReST docstring on every function (no types in
  docstrings).
- Tests only through `just test-py <files>` from the worktree's root.
  Stage files by name; never commit `uv.lock`.
- One migration, with fixed SQL, safe to run twice, safe with the old role
  names, carrying the search path `pg_catalog, public, pg_temp`. Follow the
  two newest migrations.
- A memory without typed links gives exactly the same search results as
  before.

## Review focus

Inputs the spec implies and that are most likely to bite. Each has a test
in the task named.

1. A body that starts with `---` but is not a block of key lines (a
   Markdown rule, or a block that never closes) is stored unchanged.
   Task 1.
2. `correct`, `remember` or `log` produce a body that starts with a
   `replaces` block: no link is made and the text stays. Task 4.
3. A reader who can read the old engram and not the new one: same rank as
   before, no `replaced_by`, also not in the index line. Tasks 5 and 6.
4. The second `write` call brings a token that was staged for other
   engrams or other pairs: refused with `stale_token`, nothing written.
   Task 4.
5. A `write` call of three engrams where one adds a `replaces` link: after
   the first call none of the three exists or has changed. Task 4.

---

### Task 1: Read the block

**Files:**
- Create: `src/reef/relations.py`
- Test: `tests/test_relations.py`

**Interfaces:**
- Produces:
  - `RELATION_KEYS: dict[str, LinkKind]`, in this order: `replaces` →
    `SUPERSEDES`, `contradicts` → `CONTRADICTS`, `about` → `ABOUT`,
    `part_of` → `PART_OF`.
  - `MAX_RELATIONS = 20`
  - `class InvalidRelations(ValueError)` with attribute `code`:
    `relation_invalid` or `relation_too_many`.
  - `@dataclass(frozen=True) class Relations` with one field
    `given: dict[LinkKind, tuple[str, ...]]`. A key that is absent was not
    mentioned; an empty tuple means "remove all of this kind". Values are
    wiki-link targets as written, without the brackets, in order, without
    repeats. Property `empty: bool` is true when `given` is empty.
  - `def take_block(body: str) -> tuple[str, Relations]`

- [x] **Step 1: Write the failing tests** in `tests/test_relations.py`,
  one test per line of this table, plus one generated test.

  | Body | Returned body | `given` |
  |---|---|---|
  | `"---\nreplaces: [[a.md]]\n---\n# T\n"` | `"# T\n"` | `{SUPERSEDES: ("a.md",)}` |
  | `"---\nreplaces: [[a.md]], [[fam:b.md]]\n---\nx"` | `"x"` | `{SUPERSEDES: ("a.md", "fam:b.md")}` |
  | `"---\nabout:\n  - [[a.md]]\n  - [[b.md]]\n---\nx"` | `"x"` | `{ABOUT: ("a.md", "b.md")}` |
  | `"---\ntitle: T\nreplaces: [[a.md]]\ntags: [x]\n---\nx"` | `"---\ntitle: T\ntags: [x]\n---\nx"` | `{SUPERSEDES: ("a.md",)}` |
  | `"---\nreplaces:\n---\nx"` | `"x"` | `{SUPERSEDES: ()}` |
  | `"---\ntitle: T\n---\nx"` | unchanged | `{}` |
  | `"# T\n\n---\nreplaces: [[a.md]]\n---\n"` (not at the top) | unchanged | `{}` |
  | `"---\n\nA rule, then text.\n"` (never closes) | unchanged | `{}` |
  | `"---\r\nreplaces: [[a.md]]\r\n---\r\nx"` | `"x"` | `{SUPERSEDES: ("a.md",)}` |
  | `"---\nreplaces: [[a.md]], [[a.md]]\n---\nx"` | `"x"` | `{SUPERSEDES: ("a.md",)}` |
  | `"---\nreplaces: a.md\n---\nx"` | raises `relation_invalid` | |
  | `"---\nreplaces: [[a.md]] and more\n---\nx"` | raises `relation_invalid` | |
  | a `part_of` line with 21 links | raises `relation_too_many` | |

  Generated: 2,000 bodies built from lines that hold none of the four keys
  at the start of a line, with and without a leading block; assert the body
  comes back identical and `given == {}`.

- [x] **Step 2: Run** `just test-py tests/test_relations.py`. Expected:
  fails, no module `reef.relations`.
- [x] **Step 3: Implement `take_block`.** Read lines, not YAML: the block
  is the text between a first line `---` and the next line `---`. Inside
  it, a line `key:` at column 0 with one of the four keys starts a
  relation; its value is the rest of the line, plus following lines that
  start with white space and `- `. Remove exactly those lines. If the block
  holds no other line, remove the block and the one line break after it.
  Keep the body's own line endings.
- [x] **Step 4: Run** the tests. Expected: pass.
- [x] **Step 5: Commit** `feat(links): read relations from a front matter block (RUG-36)`.

---

### Task 2: The database rules

**Files:**
- Modify: `src/reef/rls.py` (`_LINK_WRITE`, `link_statements`)
- Create: `src/reef/piccolo_migrations/reef_2026_11_26t15_00_00_000000.py`
- Test: `tests/test_typed_links_db.py`, `tests/test_migration_chain.py`

**Interfaces:**
- Produces:
  - Insert policy: a row with `kind = 'supersedes'` is accepted only when
    the target engram is in a space the caller may write in
    (`reef_member_space_ids()`). Other kinds as today.
  - `reef_retire_typed_links(p_engram uuid, p_kind text, p_keep uuid[])
    RETURNS void`, security definer, built with `_function_ddl`. It marks
    as `superseded` (with `superseded_at`) every link that is active, has
    `source = 'author'`, the given kind, starts at `p_engram`, does not
    point at an engram in `p_keep`, and points at an engram in a space the
    caller is in. It does nothing unless the caller may change `p_engram`
    (same check as `reef_retire_links`). It returns nothing.

- [x] **Step 1: Write the failing tests**, through the `probe` connection
  (the stand-in for the app role), armed as a person:
  - a `supersedes` row to an engram in a space where the caller is only a
    viewer is refused; the same row with `kind = 'about'` is accepted;
  - a `supersedes` row to an engram in a space where the caller is a
    member is accepted;
  - `reef_retire_typed_links` retires the kind it is given and leaves
    wiki links and other kinds alone;
  - it keeps what is in `p_keep`;
  - called by someone who may not change the engram, it changes nothing;
  - it leaves a link whose target is in a space the caller is not in
    (set that up with a second member, read the result through `seed`).
- [x] **Step 2: Run** them. Expected: fail (function missing, policy
  accepts the viewer case).
- [x] **Step 3: Change `reef.rls`** and write the migration: drop and
  recreate `links_insert`, create the function with its owner and grants as
  `reef_retire_links` has them. Say in the migration's docstring what it
  changes.
- [x] **Step 4: Run** `just test-py tests/test_typed_links_db.py
  tests/test_migration_chain.py tests/test_helper_search_path.py`.
  Expected: pass.
- [x] **Step 5: Commit** `feat(links): database rules for typed links (RUG-36)`.

---

### Task 3: Store relations on save

**Files:**
- Modify: `src/reef/links.py`, `src/reef/engrams.py`,
  `src/reef/methods/__init__.py` (messages), `src/reef/methods/refusals.py`
- Test: `tests/test_typed_links.py`

**Interfaces:**
- Consumes: `Relations` (Task 1), the policy and function of Task 2.
- Produces:
  - `class RelationRefused(Exception)` in `reef.links`, with `code` and
    `target` (the link as written): `relation_target_missing`,
    `relation_into_personal`, `relation_to_itself`,
    `relation_needs_write`, `relation_circle`.
  - `async def sync_typed_links(resolver: Resolver, engram_id: UUID,
    space_id: UUID, relations: Relations) -> None`
  - `async def pending_replacements(resolver: Resolver, space_id: UUID,
    path: str, relations: Relations) -> list[tuple[str, str]]`: the
    `replaces` targets that are not yet active links from the engram at
    that path (which may not exist yet), as `(space name, path)` pairs in
    the caller's names, sorted. It raises `RelationRefused` for the same
    reasons a save would.
  - `save_engram(..., relations: Relations | None = None)`: when given and
    not empty, calls `sync_typed_links` in the same transaction, after the
    wiki links.
  - `MESSAGES` gains one text per code above and for `relation_invalid`
    and `relation_too_many`. `refusals` maps `RelationRefused` and
    `InvalidRelations` to `MethodError`.

- [x] **Step 1: Write the failing tests** against `save_engram`:
  - each kind becomes one active row with that kind and `source =
    'author'`; `links_of` shows it with its kind;
  - a save without `relations`, or with a kind absent, leaves existing
    typed links alone; a kind with an empty tuple retires its links; a
    kind with a new list retires what is no longer listed and keeps the
    rest (same row, not a new one);
  - each refusal of the table above, with nothing saved (the engram's
    version and body are as before);
  - a circle: A replaces B, then B replaces A is refused; A replaces B,
    B replaces C, C replaces A is refused;
  - two engrams may replace the same one;
  - `relations` never touch wiki links, and a body change never touches
    typed links.
- [x] **Step 2: Run** them. Expected: fail, `save_engram` has no
  `relations`.
- [x] **Step 3: Implement.** Resolve targets with `Resolver` and
  `parse_target`; unlike `wanted`, a target that is missing or not allowed
  is refused, not skipped. Find a circle with one recursive query over the
  active `supersedes` links the caller can read. Insert with `kind` and
  `source`; retire through `reef_retire_typed_links` with the ids to keep.
- [x] **Step 4: Run** `just test-py tests/test_typed_links.py
  tests/test_links.py tests/test_engrams.py`. Expected: pass.
- [x] **Step 5: Commit** `feat(links): store typed links when an engram is saved (RUG-36)`.

---

### Task 4: `write` asks twice, the web app writes at once

**Files:**
- Modify: `src/reef/methods/capture.py` (`write`), `src/reef/server.py`
  (the `write` shell), `src/reef/web/routes_api.py` (the engram PUT route),
  `clients/python/src/reef_cli/cli.py` (the `write` command gains
  `--confirm`), `tests/test_protocol.py` if a guard needs a word
- Test: `tests/test_method_capture.py`, `tests/test_web_api_write.py`,
  `tests/test_tool_surface.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `take_block`, `pending_replacements`, `save_engram(relations=)`,
  `staged.stage` and `staged.take`.
- Produces:
  - `capture.write(caller, reason, space, engrams, message, confirm=None)`.
    It takes the block out of every engram's body first. If the call would
    add any `replaces` link and `confirm` is None: write nothing, and
    return `{"effect": str, "token": str, "expires_in": int}`. The effect
    names each pair, old path and new path, and says the old engram stays
    readable and ranks lower. The token's subject is the sorted list of
    pairs. With `confirm`: take the token for the action `write`, compare
    its subject with the pairs of this call, refuse with `stale_token` on
    any difference, then write as today with the relations.
  - The `write` tool's description gains two or three sentences: the four
    keys, and that `replaces` takes two calls and the person's yes.
  - The web route: take the block, save with the relations, no second
    step. A refusal comes back as the route's errors do today.

- [x] **Step 1: Write the failing tests:**
  - first call with a `replaces` block: the answer has the three keys,
    and no engram of the call exists or changed (use three engrams);
  - second call with the token: all are written, the link is active, the
    stored body has no `replaces` line;
  - second call with other pairs, with a token of another person, with a
    token for another action: `stale_token`, nothing written;
  - a call whose `replaces` link is already active needs no token;
  - `contradicts`, `about`, `part_of` and `replaces:` (empty) need no
    token;
  - `correct`, `remember` and `log` that leave a `replaces` block at the
    top of a body: no link, the text is stored as it is;
  - the web route: a person's save with a block writes at once and
    returns the body without the lines; a refusal is a 400 with the
    message;
  - the tool surface lists `confirm` on `write`; the CLI's `write` sends
    it.
- [x] **Step 2: Run** them. Expected: fail.
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** the four test files and `tests/test_protocol.py`.
  Expected: pass.
- [x] **Step 5: Commit** `feat(write): "replaces" takes the person's yes; the web app writes at once (RUG-36)`.

---

### Task 5: Search ranks a replaced engram lower

**Files:**
- Modify: `src/reef/links.py`, `src/reef/search.py`
- Test: `tests/test_search_replaced.py`

**Interfaces:**
- Produces:
  - `async def replacements(engram_ids: list[UUID]) -> dict[UUID,
    list[UUID]]` in `reef.links`: for each given engram that is replaced
    for the armed reader, the ends of its chains, newest link first. One
    query, recursive, over the active `supersedes` links the reader can
    read. An engram that is not replaced is not in the result.
  - `def demote(ranking: list[tuple[str, UUID]], replaced: dict[UUID,
    list[UUID]], insertable: set[UUID]) -> list[tuple[str, UUID]]` in
    `reef.search`, pure: engram hits that are replaced go after the engram
    hits that are not, each group in its order; file hits keep their
    positions. For a replaced hit none of whose replacements is in the
    ranking, the first replacement that is in `insertable` is put directly
    before it.
  - Each engram hit that is replaced carries `replaced_by`: a list of
    `{"space", "path", "title"}` in the reader's names. Hits that are not
    replaced do not carry the key.

- [x] **Step 1: Write the failing tests:**
  - `demote` alone: the order rule; files keep their positions; insertion;
    no insertion when the replacement is not in `insertable`; an empty
    `replaced` returns the ranking itself;
  - through `search_engrams`: the old engram ranks after the new one
    although its words fit better, and carries `replaced_by`;
  - a chain A, B, C gives C's `replaced_by` as A;
  - two replacements give a list of two, newest link first;
  - the replacement is put in when it was not found by the words, and the
    list is still at most `limit` long;
  - a search in one space does not put in a replacement from another
    space, and still carries the note;
  - a reader who cannot read the replacement: same ranking as without any
    link, no `replaced_by`;
  - with reading inside files on: the rule of five still holds
    (`tests/test_search_spread.py` shows how to check it);
  - generated: with no `supersedes` link anywhere, the ranking for 20
    queries is identical to the ranking before this task (compare against
    `search_engrams` with `demote` patched to return its input).
- [x] **Step 2: Run** them. Expected: fail.
- [x] **Step 3: Implement.** Apply `demote` to the ranking after the
  reranker and before the last `spread` and the cut to `limit`. Ask
  `replacements` once, for the engram ids in the ranking. `insertable` is
  the replacements that lie in the searched spaces.
- [x] **Step 4: Run** `just test-py tests/test_search_replaced.py
  tests/test_search.py tests/test_search_files.py tests/test_search_spread.py
  tests/test_rerank.py tests/test_search_hybrid.py`. Expected: pass.
- [x] **Step 5: Commit** `feat(search): a replaced engram ranks after the others and names its replacement (RUG-36)`.

---

### Task 6: Say it where an engram is shown

**Files:**
- Modify: `src/reef/methods/find.py` (`recall`, `open`, `brief`,
  `_engram_line`), `src/reef/methods/connector.py`, `src/reef/context.py`,
  `src/reef/protocol.md`
- Test: `tests/test_method_find.py`, `tests/test_method_brief.py`,
  `tests/test_connector_tools.py`, `tests/test_protocol.py`

**Interfaces:**
- Consumes: `replacements` and the `replaced_by` of search hits (Task 5).
- Produces:
  - `recall` passes `replaced_by` through on each source that has it.
  - `open` on an engram and the connector's `fetch`: `replaced_by` when
    the engram is replaced for this reader.
  - `brief`: each item for a replaced engram carries `replaced_by`.
  - The index line of a replaced engram ends with ` (replaced by <path>)`,
    with the first replacement; `<path>` has the space's name in front when
    the replacement is in another space. One query for the whole index.
  - `protocol.md`: a short passage after the one on wiki links: the four
    keys with one example; `replaces` takes two calls and the person's yes;
    a replaced engram is out of date, so answer from its replacement and
    add new facts there.

- [x] **Step 1: Write the failing tests**, one per line above, and for a
  reader who cannot read the replacement: no key and no note in any of
  them.
- [x] **Step 2: Run** them. Expected: fail.
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** the four test files. Expected: pass.
- [x] **Step 5: Commit** `feat(tools): a replaced engram says so in recall, open, brief and the index (RUG-36)`.

---

### Task 7: A test conversation, the changelog, and the whole suite

**Files:**
- Modify: `tests/conversations/scenarios.toml`, the made-up person's
  fixture under `src/reef/testaccount/` (two engrams about one contract,
  an old and a new one, and the fixture's version), `changes/`
- Test: `tests/test_conversations_scoring.py`, the fixture's tests

- [x] **Step 1: Add the scenario** `replace-contract`, kind `write`: the
  person says the new engram replaces the old one and asks to record it;
  then says yes; then asks a question whose answer differs between the two
  engrams. `tools_required` holds `write`; the fact is the new engram's.
  Follow the scenarios `forget-confirmed` and `share-schedule` for a tool
  that asks twice.
- [x] **Step 2: Run** `just test-py tests/test_conversations_scoring.py`
  and the fixture's tests. Expected: pass.
- [x] **Step 3: Add** `changes/feat-rug-36-typed-links.md`, kind `added`,
  for a person who uses Reef: an engram can replace another one, the old
  one stays and ranks lower.
- [x] **Step 4: Run** `just test-py` and `just lint`. Expected: both pass.
- [x] **Step 5: Commit** `test(conversations): a person replaces one engram by another (RUG-36)`.

Not for the worker: the private question set and the benchmark are run by
the coordinator after the build, before and after the change, and must give
the same scores.
