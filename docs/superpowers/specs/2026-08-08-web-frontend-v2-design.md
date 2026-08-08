# Web frontend v2 — design

Written 8 Aug 2026, same day v1 landed. v1 proved the surface; v2 makes it
feel like a product. Two changes, decided through mockup rounds (round 1:
three visual directions; round 2: the chosen hybrid across every screen):
a **visual system** (calm-document structure + writing-first typography)
and a **reorganization** around two ideas — a real two-pane desktop app,
and **members visible from everywhere**. Everything else from the v1 spec
(`2026-08-08-web-frontend-design.md`) stands: same auth, same API-over-RLS
architecture, same Bun+React toolchain, same protections.

## What stays true

- **Members are per space.** Access stays space-level; RLS unchanged. v2
  changes *visibility* of membership, never its granularity.
- The API surface is v1's, plus one read-side addition (last editor, below).
- `meta/` pages stay read-only in the UI; disclosure text stays loud;
  removal stays two-step inline (no browser dialogs).
- Mobile remains first-class: every screen works at 390 px.

## Visual system ("the hybrid")

- **Shell (from direction A, calm document):** ground `#fbfcfd`; sidebar
  `#f2f7f8`; hairlines `#e5edf0`/`#eef3f5`; ink `#1c2b33`; muted `#7b8a92`;
  seafoam accent `#0d9488` with soft fill `#e7f9f4` (the existing brand
  palette). Soft depth (rounded 8–12 px, one gentle shadow tier), obvious
  tappable rows, avatar presence everywhere. Sans-serif chrome (system
  stack as today).
- **Reading (from direction C, writing-first):** page bodies and page
  titles render in a serif stack (`Georgia, "Iowan Old Style", serif`),
  ~16.5 px / 1.6–1.65 line height, measure capped ≈ 620 px. Metadata is a
  quiet sentence, not widgets: "seen by everyone in reef · edited by
  Wouter, 2 h · v2".
- **Avatars:** initial-on-color circles, 26 px (20 px small), color
  deterministically hashed from the display name into a fixed 8-color
  palette; overlapping stacks (−8 px) with a `+N` overflow chip beyond 4.
- **Dark variant** maintained for the whole system (tokens flip; serif
  reading stays serif).
- The reef mark, favicon, and wordmark stay as shipped.

## Organization

- **Desktop (≥ 900 px): two-pane shell.** Left sidebar: brand; Spaces list
  (the open space's row shows its member avatar stack; other rows show
  their page count — one members fetch, not one per space);
  the open space's pages nested beneath it; "New page" / "New space";
  account row (display name + sign out) pinned at the bottom. Right pane:
  the current page in reading view, or the space's page list when no page
  is open. The v1 Home screen dissolves into the sidebar on desktop.
- **Mobile (< 900 px): stacked navigation** as today — Spaces → Space →
  Page — with the same components; the sidebar's content IS the Spaces
  and Space screens.
- **Members from everywhere:** the avatar stack appears in (1) each
  sidebar space row, (2) every space header with the sentence
  "N members see everything" + a Manage affordance, (3) every page
  header/bar. Tapping any of them opens **one shared members sheet**
  (bottom sheet on mobile, right-side panel on desktop): roster with
  avatars, display names, and — for the owner — emails and two-step
  Remove; invite form (email + optional display name); the returned
  disclosure rendered as a warning callout. Non-owners see the roster
  (display names only, as the API already enforces) with no controls.
  The sheet header restates the model: "Everyone sees everything — past
  and future. There is no per-page hiding."
- **Page reading view:** serif title, metadata sentence, serif body,
  tag chips at the end, Edit in the page bar (hidden for `meta/`, which
  shows the protected note instead).
- **Editor:** unchanged mechanics (monospace body, preview toggle, live
  index-description line, optional "why" message, 409 conflict flow),
  restyled into the system; versioning shown quietly ("v2 · saves as v3").

## Backend addition (the only one)

The metadata sentence needs "edited by <name>". Revisions already store
`author_id`; expose it read-side:

- `GET /api/pages/{space}/{path}` gains `last_editor: str | null` — the
  display name of the latest revision's author (null when no revision
  author resolves).
- Index page rows gain the same `last_editor` field (one joined query,
  computed in `build_index` alongside the existing fields), so lists can
  show "Today · Wouter" without N+1 fetches. MCP payloads gain the field
  too — additive, no MCP consumer breaks.

No other endpoint changes. No presence, no live collaboration.

## Component structure (frontend)

Refactor v1's per-screen CSS into a small token + component layer:

- `tokens.css` — the palette/type/spacing custom properties (light+dark).
- `components/`: `Avatar` + `AvatarStack` (hashing lives here),
  `MembersSheet` (the one shared sheet, both owner and member modes),
  `PageMeta` (the metadata sentence), `Sidebar` (desktop shell),
  `AppShell` (chooses two-pane vs stacked by viewport).
- Views keep their names; Home becomes the mobile Spaces screen and the
  sidebar's data source (one shared index fetch via a small context —
  today three views fetch `/api/index` independently; v2 lifts it into an
  app-level provider with manual refresh on mutations).

## Error handling & testing

- Error/loading conventions unchanged (inline notices, no toasts).
- Backend: tests for `last_editor` on page GET and index (author resolves;
  null when the author's person row is gone).
- Frontend: `bun test` for the avatar hash (deterministic, collision-free
  across the palette for typical names) and the metadata-sentence builder;
  existing markdown/summary tests unchanged. Manual Playwright pass at
  390 px and ≥ 900 px (two-pane) before completion.

## Out of scope (v2)

- Per-page permissions (explicitly rejected — v1 model stands).
- Presence/live editing, comments, notifications.
- Revision history UI (still deferred; `last_editor` is metadata only).
- Search, keyboard command bar (direction B ideas — later).
