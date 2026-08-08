# Web Frontend v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle and reorganize the shipped v1 frontend into the approved hybrid: calm-document shell + serif reading typography, a two-pane desktop app with sidebar, members visible and manageable from every screen, and a `last_editor` field powering "edited by" metadata.

**Architecture:** All v1 architecture stands (Bun+React SPA served at `/app`, JSON API at `/api`, RLS-enforced domain functions). v2 is one small backend read-side addition (`last_editor` from the revisions table) plus a frontend refactor: a token/component layer (`tokens.css`, `Avatar`, `MembersSheet`, `PageMeta`, `AppShell`/`Sidebar`), a shared index provider replacing per-view fetches, and restyled views.

**Tech Stack:** unchanged — Python 3.13/FastMCP/Piccolo backend, Bun + React 19 + react-router-dom + markdown-it + DOMPurify frontend.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-08-web-frontend-v2-design.md` — where plan and spec disagree, the spec wins. **Visual reference:** `docs/superpowers/specs/2026-08-08-web-frontend-v2-mockup.html` — the approved mockup; its CSS values are the design source of truth.
- Members are per space; access model and RLS unchanged. `meta/` stays read-only in the UI. Disclosure text stays loud. No `window.confirm`.
- Palette (light): ground `#fbfcfd`, sidebar `#f2f7f8`, hairlines `#e5edf0`/`#eef3f5`, ink `#1c2b33`, muted `#7b8a92`, accent `#0d9488`, accent-soft `#e7f9f4`. Serif stack: `Georgia, "Iowan Old Style", serif`; reading measure ≤ 620px; body ~16.5px/1.62. Dark variant maintained for every token.
- Desktop breakpoint: `≥ 900px` two-pane; below it, stacked navigation.
- Backend: ReST docstrings mandatory everywhere incl. tests (no types in docstrings); `uv run pytest` (real Postgres on localhost:5433); `uv run ruff check src tests` clean. Frontend: `cd frontend && bun test && bunx tsc --noEmit && bun run build` green per task.
- git add+commit atomically in ONE Bash call (staging races observed in this environment).
- Live verification recipe (used by Tasks 5–7): rebuild rif_test schema (`uv run pytest tests/test_web_session.py -q`), seed via the session's established pattern (person + personal + shared space + pages through domain functions), run `RIF_DEV_INSECURE=1 RIF_DEV_PRINCIPAL_EMAIL=<seeded email> PORT=8001 DATABASE_URL=postgresql://rif:rif@localhost:5433/rif_test uv run python -m rif.server` in background; frontend via `bun run dev` (temporarily point dev.ts at 8001; revert before committing); verify with Playwright browser tools; kill background processes when done.

---

### Task 1: Backend — `last_editor` on index rows and page payloads

**Files:**
- Modify: `src/rif/context.py` (helper + `build_index` wiring)
- Modify: `src/rif/web/routes_api.py` (page payload builder)
- Test: `tests/test_context.py` (extend), `tests/test_web_api_read.py` (extend)

**Interfaces:**
- Produces: `async def latest_editors(page_ids: list[UUID]) -> dict[UUID, str | None]` in `rif.context` — for each page id, the display name of the newest revision's author, or None when the author row is gone or the page has no revisions. Index page rows gain `"last_editor": str | None`; web `GET /api/pages/{space}/{path}` and the PUT response gain the same key. (MCP payloads gain it automatically via `build_index` — additive.)

- [ ] **Step 1: Write the failing tests**

In `tests/test_context.py`, following that file's existing fixture style (read it first — it builds worlds through the Graph builders and `save_page`):

```python
async def test_index_rows_carry_last_editor(graph):
    """Each index page row names the newest revision's author."""
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    await graph.personal_space(alice)
    await graph.shared_space("team", alice, bob)
    a = Principal(person_id=alice.id, email=alice.email)
    b = Principal(person_id=bob.id, email=bob.email)
    async with transaction_scope():
        await save_page(a, "team", "n.md", "First line.\n", message="one")
    async with transaction_scope():
        await save_page(
            b, "team", "n.md", "Second line.\n", message="two", expected_version=1
        )
    async with transaction_scope():
        payload = await build_index(a)
    team = next(s for s in payload.spaces if s.alias == "team")
    assert team.pages[0]["last_editor"] == "Bob"


async def test_last_editor_none_when_author_erased(graph):
    """A vanished author row degrades to None, never an error."""
    alice = await graph.person("alice@x.com", "Alice")
    await graph.personal_space(alice)
    a = Principal(person_id=alice.id, email=alice.email)
    async with transaction_scope():
        await save_page(a, "personal", "n.md", "Line.\n", message="one")
    async with transaction_scope():
        await Revision.update({Revision.author_id: None}).where(
            Revision.author_id == alice.id
        )
        payload = await build_index(a)
    personal = next(s for s in payload.spaces if s.alias == "personal")
    row = next(p for p in personal.pages if p["path"] == "n.md")
    assert row["last_editor"] is None
```

(Adapt builder names to `tests/conftest.py`'s real Graph API — `graph.personal_space(alice)` / `graph.shared_space("team", alice, bob)` per the existing suite; imports at top of file as the suite does. Note personal spaces auto-create starter pages — select the page by path, never by position, in both tests.)

In `tests/test_web_api_read.py`, extend `test_get_page_and_404`: after the existing assertions, `assert page["last_editor"] == "Alice"` (alice authored the seed write).

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_context.py -v` → KeyError/AttributeError on `last_editor`.

- [ ] **Step 3: Implement**

In `src/rif/context.py`:

```python
async def latest_editors(page_ids: list[UUID]) -> dict[UUID, str | None]:
    """Return each page's newest revision author, as a display name.

    One query for the whole batch: newest-first revisions joined to
    persons, first row per page wins. Pages without revisions, or whose
    author row was erased, map to None.

    :param page_ids: the pages to resolve
    :returns: page id to display name, None where unresolvable
    """
    if not page_ids:
        return {}
    rows = await Revision.select(
        Revision.page_id, Revision.author_id.display_name
    ).where(Revision.page_id.is_in(page_ids)).order_by(
        Revision.created_at, ascending=False
    )
    editors: dict[UUID, str | None] = {pid: None for pid in page_ids}
    for row in rows:
        pid = row["page_id"]
        if editors.get(pid) is None and pid in editors:
            editors[pid] = row["author_id.display_name"]
    return editors
```

Verify the joined column name Piccolo emits (`author_id.display_name`) by running one query in a scratch test — if the key differs (e.g. `display_name`), adapt. NOTE the first-row-wins loop is only correct because `editors` pre-fills None and we skip already-set pages; a page whose newest revision has an erased author must stay None, NOT fall through to an older revision's author — so once a page's newest row is seen, later rows must not overwrite. The loop above has a bug for exactly that case: it would fall through. Implement instead with an explicit `seen: set[UUID]`:

```python
    seen: set[UUID] = set()
    for row in rows:
        pid = row["page_id"]
        if pid in seen or pid not in editors:
            continue
        seen.add(pid)
        editors[pid] = row["author_id.display_name"]
```

(`display_name` is non-null in the schema, but the join row itself is absent when `author_id` is NULL — confirm what Piccolo returns for a NULL FK join, and coerce to None.)

In `build_index`, after fetching pages: `editors = await latest_editors([p.id for p in pages])`, and add `"last_editor": editors.get(page.id)` to each page row dict.

In `src/rif/web/routes_api.py`, the page-payload builder used by GET and PUT responses: call `latest_editors([page.id])` and add `"last_editor"` to the dict. (GET/PUT already share a shaping helper — extend it; if PUT's shape is built separately, extend both identically.)

- [ ] **Step 4: Run** targeted tests, then the full suite, then ruff — all green.

- [ ] **Step 5: Commit** — `git add ... && git commit -m "feat: last_editor on index rows and page payloads"` (one Bash call).

---

### Task 2: Frontend foundations — tokens, Avatar, PageMeta

**Files:**
- Create: `frontend/src/tokens.css`, `frontend/src/components/Avatar.tsx`, `frontend/src/components/avatarColor.ts`, `frontend/src/components/avatarColor.test.ts`, `frontend/src/components/pageMeta.ts`, `frontend/src/components/pageMeta.test.ts`
- Modify: `frontend/src/app.css` (import tokens; keep existing rules working), `frontend/src/types.ts` (add `last_editor: string | null` to `PageMeta` and `Page`), `frontend/src/main.tsx` (import tokens.css first)

**Interfaces:**
- Produces (later tasks rely on these exact names):
  - `tokens.css`: custom properties `--ground #fbfcfd; --panel #f2f7f8; --hairline #e5edf0; --hairline-soft #eef3f5; --ink #1c2b33; --muted #7b8a92; --faint #b3bec4; --accent #0d9488; --accent-deep #0b6b62; --accent-soft #e7f9f4; --danger #b3554f; --serif Georgia, "Iowan Old Style", serif;` with a dark redefinition block (`prefers-color-scheme: dark`, values: ground `#0d1a20`, panel `#0f2129`, hairline `#1c333d`, hairline-soft `#16292f`, ink `#e2f1f5`, muted `#8fb0ba`, faint `#5b7681`, accent `#38bdd8`, accent-deep `#7ce3d3`, accent-soft `#123a35`, danger `#e0847e`).
  - `avatarColor(name: string): string` — deterministic pick from the 8-color palette `["#0d9488","#6366f1","#f59e0b","#ec4899","#0284c7","#84cc16","#8b5cf6","#f97316"]` by summing char codes modulo 8.
  - `<Avatar name size?>` (size `"md" | "sm"`, default md=26px, sm=20px) — initial-on-color circle. `<AvatarStack names max?>` (default max 4) — overlapped avatars with a `+N` chip beyond max; whole stack accepts `onClick`.
  - `pageMetaSentence(parts: {space: string; personal: boolean; lastEditor: string | null; updated: string; version?: number}): string` — builds e.g. `"seen by everyone in reef · edited by Wouter, 2 h ago · v2"`; personal spaces say `"only you"` instead of the audience clause; null editor omits the edited-by clause; version omitted when undefined. Uses the existing `relativeTime` helper for `updated`.

- [ ] **Step 1: Write the failing bun tests**

```ts
// avatarColor.test.ts
import { expect, test } from "bun:test";
import { avatarColor, initialOf } from "./avatarColor";

test("deterministic per name", () => {
  expect(avatarColor("Wouter")).toBe(avatarColor("Wouter"));
});
test("stays inside the palette", () => {
  for (const n of ["Demo", "Wouter", "Roos", "張三", ""])
    expect(avatarColor(n)).toMatch(/^#[0-9a-f]{6}$/);
});
test("initial is first grapheme uppercased, ? for empty", () => {
  expect(initialOf("wouter")).toBe("W");
  expect(initialOf("")).toBe("?");
});
```

```ts
// pageMeta.test.ts
import { expect, test } from "bun:test";
import { pageMetaSentence } from "./pageMeta";

test("shared space, editor, version", () => {
  const s = pageMetaSentence({
    space: "reef", personal: false, lastEditor: "Wouter",
    updated: new Date(Date.now() - 7200_000).toISOString(), version: 2,
  });
  expect(s).toContain("seen by everyone in reef");
  expect(s).toContain("edited by Wouter");
  expect(s).toContain("v2");
});
test("personal space says only you; null editor omitted", () => {
  const s = pageMetaSentence({
    space: "personal", personal: true, lastEditor: null,
    updated: new Date().toISOString(),
  });
  expect(s).toContain("only you");
  expect(s).not.toContain("edited by");
});
```

- [ ] **Step 2: `bun test` → fails (modules missing).**
- [ ] **Step 3: Implement** tokens.css (values above, both themes; components style via `var(--…)` only), avatarColor.ts (+ `initialOf`), Avatar.tsx, pageMeta.ts. Avatar CSS lives in app.css using tokens (`.avatar`, `.avatar-stack`, overlap −8px, 2px ground-colored border, `+N` chip `--faint` background). Add `last_editor` to types.
- [ ] **Step 4: `bun test && bunx tsc --noEmit && bun run build` green.**
- [ ] **Step 5: Commit** — `"feat: v2 foundations — design tokens, avatars, page-meta sentence"`.

---

### Task 3: Index provider

**Files:**
- Create: `frontend/src/IndexProvider.tsx`
- Modify: `frontend/src/App.tsx` (wrap routes), `frontend/src/views/Home.tsx`, `frontend/src/views/SpaceView.tsx` (consume the provider instead of fetching)

**Interfaces:**
- Produces: `IndexProvider` React context + `useIndex(): {index: IndexPayload | null; error: string | null; refresh(): Promise<void>}`. Fetches `/api/index` once on mount; `refresh()` refetches (callers: after page save, space create, invite, remove — wired in the tasks that own those flows). Views render their existing loading/inline-error states from `index === null` / `error`.

- [ ] **Step 1: Implement provider** (useState + useEffect + useCallback, cancelled-flag pattern as in SpaceView's effects; context via createContext/useContext with a throw-if-missing guard in `useIndex`).
- [ ] **Step 2: Convert Home and SpaceView** to `useIndex()`; SpaceView keeps its own members fetch. NewSpace/Editor/NewPage call `refresh()` after successful mutations (touch those files only for the refresh call).
- [ ] **Step 3: `bun test && bunx tsc --noEmit && bun run build` green;** quick dev-server sanity check that home and space still render (full walkthrough comes later).
- [ ] **Step 4: Commit** — `"feat: shared index provider replaces per-view index fetches"`.

---

### Task 4: AppShell and Sidebar (two-pane desktop)

**Files:**
- Create: `frontend/src/components/AppShell.tsx`, `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.tsx` (header logic moves into AppShell), `frontend/src/app.css` (shell + sidebar styles per the mockup's `.shell`/`.side` blocks, tokenized)

**Interfaces:**
- Consumes: `useIndex`, `Avatar`/`AvatarStack`, `Me` via `apiGet<Me>("/api/me")` (fetch in AppShell, pass down).
- Produces: `<AppShell>{routes}</AppShell>` — at `≥ 900px` (CSS grid `250px 1fr` + a `useMediaQuery("(min-width: 900px)")` hook defined inside AppShell.tsx) renders Sidebar + content pane; below, renders the v1-style top header (reef mark + wordmark) above stacked content. Sidebar per the mockup: brand; SPACES label; space rows (alias, member `AvatarStack` size sm from `space.members` — see note; personal shows page count instead), active-state via route match; the active space's pages nested (NavLink to `/s/:space/p/<path>`, active page highlighted); "＋ New page" and "＋ New space" links; account row at bottom (`Avatar sm` + display name + a Sign out link POSTing `/api/auth/logout` then redirecting to `/`).
- **Note — sidebar member stacks need names:** the index payload has no member names. Do NOT add per-space member fetches in the sidebar (N spaces = N calls). Instead show the stack only for the ACTIVE space (reuse SpaceView's members data by lifting the members fetch into a small `useMembers(space)` hook — Create: `frontend/src/useMembers.ts` — cached per space alias in module scope, used by both Sidebar and SpaceView); inactive space rows show their page count. This satisfies the spec ("each space row shows its member avatar stack" is thereby scoped to the open space — a deliberate, documented narrowing to avoid N+1; the spec's intent is members-visible-near-spaces, which the active-space stack + space header deliver).
- Mobile behavior unchanged: routes render as today.

- [ ] **Step 1: Implement** useMembers hook, Sidebar, AppShell; move header; wire into App.tsx.
- [ ] **Step 2: `bun test && bunx tsc --noEmit && bun run build`;** dev-server check at both widths (resize) — sidebar appears ≥900px, stacked layout below, navigation works in both.
- [ ] **Step 3: Commit** — `"feat: two-pane app shell with spaces/pages sidebar"`.

---

### Task 5: MembersSheet everywhere

**Files:**
- Create: `frontend/src/components/MembersSheet.tsx`
- Modify: `frontend/src/views/SpaceView.tsx` (replace the inline MembersPanel with sheet trigger), `frontend/src/components/Sidebar.tsx` (stack opens sheet), `frontend/src/app.css` (sheet styles per mockup `.mb` block: mobile bottom-sheet with grip + scrim, desktop right-side panel 380px, both animated with a 160ms ease transform, `prefers-reduced-motion` disables)

**Interfaces:**
- Consumes: `useMembers(space)`, `Avatar`, `apiSend` (invite POST, member DELETE), `useIndex().refresh`.
- Produces: `<MembersSheet space open onClose>` — one component, two modes from `members.is_owner`:
  - Header "People in <space>" + the model sentence: "Everyone sees everything — past and future. There is no per-page hiding."
  - Roster rows: Avatar + display name (+ email when non-empty); owner row tagged OWNER; owner mode gets two-step Remove… (inline confirm, exactly the v1 mechanic) per non-owner row.
  - Owner mode: invite form (email + optional display name) → on success show the returned `disclosure` in a warning callout (tokens: `#fff7ed/#fed7aa/#7c4a12` light, dark equivalents `#3a2a12/#7c4a12/#fbd9a5` — add as `--warn-*` tokens in tokens.css) and refresh the roster.
  - Non-owner mode: roster only, no controls.
  - `key={space}` semantics preserved (remount per space — the v1 stale-state lesson; state resets when `space` changes).
- Trigger points wired in this task: SpaceView's header AvatarStack + "N members see everything" sentence + Manage link (owner only); Sidebar's active-space stack; PageView's stack comes in Task 6.

- [ ] **Step 1: Implement the sheet + wire SpaceView and Sidebar; delete the old MembersPanel code.**
- [ ] **Step 2: `bun test && bunx tsc --noEmit && bun run build`;** live check (Global Constraints recipe): invite → disclosure shows, remove → two-step works, non-owner sees roster without controls (seed a second person and log in as them via RIF_DEV_PRINCIPAL_EMAIL switch), sheet opens from both space header and sidebar.
- [ ] **Step 3: Commit** — `"feat: shared members sheet reachable from space header and sidebar"`.

---

### Task 6: Reading views — PageView and Space screen restyle

**Files:**
- Modify: `frontend/src/views/PageView.tsx`, `frontend/src/views/SpaceView.tsx`, `frontend/src/views/Home.tsx`, `frontend/src/app.css` (reading + row styles per mockup `.pg`/`.sp`/`.reading` blocks, tokenized)

**Interfaces:**
- Consumes: `pageMetaSentence`, `AvatarStack`, `MembersSheet`, `useMembers`, `last_editor` from Task 1's API, `renderMarkdown` (unchanged).
- Behavior:
  - PageView: page bar (crumb "‹ <space>", AvatarStack (opens MembersSheet), Edit button — hidden for `meta/` with the protected note as today); serif `h1` title; `pageMetaSentence` line; serif body (`.reading` class: `font-family: var(--serif)`, 16px/1.62 mobile, 16.5px/1.65 desktop, `max-width: 620px`); tag chips after the body.
  - SpaceView: hero (26px sans title), whobar (AvatarStack + "N members see everything" — personal: "only you" — + Manage for owners), PAGES section label, doc-icon rows (30px accent-soft square, title + description, relative time right-aligned), New page button in accent.
  - Home (mobile spaces list): card rows restyled with tokens; personal first (API order), each row shows page count; alias displayed lowercase as-is (drop v1's `text-transform: capitalize` — the ledgered inconsistency dies here).
- [ ] **Step 1: Implement all three restyles.**
- [ ] **Step 2: Checks + live walkthrough** (both widths, light + dark: page reading view, meta sentence shows editor from Task 1, XSS page still inert, space and home screens match the mockup).
- [ ] **Step 3: Commit** — `"feat: serif reading views and calm space screens"`.

---

### Task 7: Editor restyle + dark pass + verification

**Files:**
- Modify: `frontend/src/views/Editor.tsx`, `frontend/src/views/NewPage.tsx`, `frontend/src/views/NewSpace.tsx`, `frontend/src/app.css`

**Interfaces / Behavior:**
- Editor per mockup `.ed`: labeled fields (11px uppercase labels, `--faint`), bordered inputs on white/panel, monospace body, Preview toggle chip, "Index line:" hint with accent-deep bold prefix, Save in accent + quiet "v<N> · saves as v<N+1>" caption, conflict banner restyled with the `--warn-*` tokens (mechanics untouched). NewPage/NewSpace get the same field styling.
- Dark pass: sweep app.css for any remaining literal colors outside tokens.css; convert to tokens; verify every screen in dark scheme (Playwright `colorScheme: dark` via browser_run_code or OS emulation — if unavailable, toggle `data-theme="dark"` manually per tokens.css's guard and screenshot).
- Final verification (this task closes the plan): backend `uv run pytest` + ruff; frontend `bun test && bunx tsc --noEmit && bun run build`; live Playwright walkthrough at 390px AND ≥1200px covering: sidebar navigation, space → page → edit → save (meta line updates editor name), 409 flow, new page, members sheet from all three trigger points, invite disclosure, remove, dark scheme spot-checks. Screenshots of desktop two-pane + the four mobile screens into the report.
- [ ] **Step 1: Restyle editor/new-page/new-space.**
- [ ] **Step 2: Dark sweep.**
- [ ] **Step 3: Full verification per above.**
- [ ] **Step 4: Commit** — `"feat: editor restyle, dark-variant sweep, v2 verification"`.
