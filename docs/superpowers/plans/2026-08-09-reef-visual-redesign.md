# reef Visual Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved reef identity — rename, fan-coral mark, Nunito typography, baseline lockups, list/tile Spaces views — per `docs/superpowers/specs/2026-08-09-reef-visual-redesign-design.md`.

**Architecture:** Pure-frontend change to the Bun + React 19 SPA in `frontend/`. All color already routes through `frontend/src/tokens.css`; this plan adds font/type-scale tokens there, swaps the mark geometry in one component + one static SVG, and adds a view toggle to `Home.tsx`. No backend changes.

**Tech Stack:** Bun (build/test), React 19, plain CSS custom properties, `@resvg/resvg-js` (dev-only, icon rasterization).

## Global Constraints

- Product name in UI: **reef**, always lowercase.
- Backend names stay: `pyproject.toml` name, `src/rif/`, DB names, `X-Rif-Csrf` header, Docker files — DO NOT rename.
- Mark gradient: `#0d9488 → #5eead4`, `gradientUnits="userSpaceOnUse"` with `x1="0" y1="50" x2="0" y2="16"` (objectBoundingBox degenerates on axis-aligned subpaths).
- Typeface: Nunito only. Weights: 800 wordmark/display/reading-title, 700 headings, 600 UI labels/buttons, 400 body. No Georgia/serif anywhere.
- Wordmark letter-spacing: `-0.005em`.
- Spaces noun stays "Spaces". View preference key: `localStorage["reef.spacesView"]`, values `"list" | "grid"`, default `"list"`.
- The traced mark source of truth: `docs/superpowers/specs/assets/2026-08-09-reef-fan-coral.svg`.
- Every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Nunito webfont, `--font-sans` token move, serif retirement

**Files:**
- Create: `frontend/src/fonts/nunito-latin.woff2`
- Modify: `frontend/src/tokens.css` (add `--font-sans`, remove `--serif`)
- Modify: `frontend/src/app.css:13-17` (remove `:root --font-sans` block, add `@font-face`), `:117-123` (tagline), `:838-844` (reading-title), `:852-857` (reading-body)

**Interfaces:**
- Produces: `var(--font-sans)` resolves to Nunito app-wide; `var(--serif)` no longer exists (nothing may reference it after this task).

- [ ] **Step 1: Vendor the font file**

The latin-subset variable woff2 (~39 KB, wght 200–1000) already sits in this machine's session scratchpad. Copy it; if the scratchpad is gone, re-download.

```bash
mkdir -p frontend/src/fonts
cp "/private/tmp/claude-501/-Users-wouter--superset-worktrees-22c2d070-5a59-44a3-bd9e-c770256b92a5-wild-credit/589b99b6-d0a9-47af-b2ec-af9f71a14921/scratchpad/fonts/nunito-1.woff2" frontend/src/fonts/nunito-latin.woff2
```

Fallback download (only if the copy fails):

```bash
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36'
css=$(curl -sf "https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap" -H "User-Agent: $UA")
url=$(echo "$css" | awk '/\/\* latin \*\//{getline; while ($0 !~ /}/) {print; getline}}' | grep -o 'https://[^)]*\.woff2' | sort -u | head -1)
curl -sf "$url" -o frontend/src/fonts/nunito-latin.woff2
```

Sanity: `ls -la frontend/src/fonts/` shows a ~35–45 KB file.

- [ ] **Step 2: Declare the face and retarget `--font-sans`**

In `frontend/src/app.css`, replace lines 13–17 (the `:root { --font-sans: ... }` block) with:

```css
@font-face {
  font-family: "Nunito";
  src: url("./fonts/nunito-latin.woff2") format("woff2");
  font-weight: 200 1000;
  font-style: normal;
  font-display: swap;
}
```

In `frontend/src/tokens.css`, inside the `:root` block (after `--danger: #b3554f;`, replacing the `--serif` line):

```css
  --font-sans:
    "Nunito", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    Helvetica, Arial, sans-serif;
```

Delete `--serif: Georgia, "Iowan Old Style", serif;` from `tokens.css`. The two dark blocks don't define font tokens — nothing to sync.

- [ ] **Step 3: Retire the serif styles**

In `frontend/src/app.css`:

`.app-header-tagline` (line ~117): delete the `font-family: var(--serif);` and `font-style: italic;` lines (keep size/color/margin).

`.reading-title` (line ~838): replace the whole rule with:

```css
.reading-title {
  font-size: 27px;
  font-weight: 800;
  letter-spacing: -0.005em;
  margin: 20px 0 6px;
}
```

`.reading-body` (line ~852): delete only the `font-family: var(--serif);` line.

- [ ] **Step 4: Verify**

```bash
grep -rn "Georgia\|--serif" frontend/src/ ; echo "exit=$?"
```
Expected: no matches (exit=1).

```bash
cd frontend && bun test && bun run build
```
Expected: tests pass; build succeeds and `dist/` contains a hashed `.woff2` asset (`ls dist | grep woff2` — if the URL didn't bundle, the build logs an unresolved-asset error; fix path, don't ship a broken url()).

Visual: `cd frontend && bun run dev`, open `http://localhost:3000/app/signed-out` — text renders in Nunito (rounded terminals obvious in "Signed out"), no serif anywhere.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/fonts/nunito-latin.woff2 frontend/src/tokens.css frontend/src/app.css
git commit -m "feat: self-hosted Nunito everywhere; retire Georgia serif

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Type-scale tokens and font-size migration

**Files:**
- Modify: `frontend/src/tokens.css` (`:root` only), `frontend/src/app.css` (font-size literals throughout)

**Interfaces:**
- Produces: `--text-xs|sm|base|md|lg|xl|2xl` tokens; all `app.css` `font-size` declarations reference them (exceptions listed below).

- [ ] **Step 1: Add scale tokens**

In `frontend/src/tokens.css` `:root`, after the `--font-sans` entry:

```css
  /* Type scale — the only font sizes app.css may use. */
  --text-xs: 0.72rem;   /* 11.5px — section labels, tags, captions */
  --text-sm: 0.8rem;    /* 12.8px — meta, sublines, hints */
  --text-base: 0.86rem; /* 13.8px — UI chrome: sidebar, buttons, crumbs */
  --text-md: 0.95rem;   /* 15.2px — card titles, row titles, inputs */
  --text-lg: 1.05rem;   /* 16.8px — reading body, sheet titles */
  --text-xl: 1.7rem;    /* 27.2px — view titles, reading title (mobile) */
  --text-2xl: 1.95rem;  /* 31.2px — reading title (desktop) */
```

- [ ] **Step 2: Migrate `app.css` font sizes**

Mechanical mapping — replace each `font-size` right-hand side (values from the current file):

| Current | Token | Rules |
|---|---|---|
| 10.5px, 11px, 11.5px | `var(--text-xs)` | `.side-label`, `.ed-label`, `.section-label`, `.ed-hint`, `.reading-tag`, `.mbs-owner-tag`, `.avatar` (11px), `.side-count` |
| 12px, 12.5px, 13px | `var(--text-sm)` | `.app-header-tagline`, `.space-card-sub`, `.ed-conflict`, `.ed-version-caption`, `.whobar-lbl`, `.whobar-manage`, `.page-row-desc`, `.page-row-when`, `.page-bar-crumb`, `.page-bar-edit`, `.reading-meta`, `.mbs-sub`, `.mbs-person-email`, `.mbs-remove`, `.mbs-confirm-remove`, `.mbs-cancel-remove`, `.mbs-invite button[type="submit"]`, `.mbs-disclose`, `.side-page`, `.side-newpage` |
| 13.5px, 14px | `var(--text-base)` | `.side-item`, `.side-me`, `.ed-toggle`, `.ed-save`, `.ed-input`, `.page-new`, `.editor-textarea` (13px→base is fine for mono), `.mbs-person-name`, `.mbs-invite-title` |
| 14.5px, 15px | `var(--text-md)` | `.space-card-alias`, `.page-row-title`, `.side-brand`, `.page-row-icon` |
| 16px | `var(--text-lg)` | `.mbs-title`, `.reading-body` (also its 16.5px desktop bump → same token, drop the bump), `body` keeps `font-size: 16px` as the rem root — do NOT tokenize body |
| 26px, 27px | `var(--text-xl)` | `.hero-title`, `.reading-title` |
| 30px | `var(--text-2xl)` | `.reading-title` inside `@media (min-width: 900px)` |
| 1.125rem | `var(--text-lg)` | `.app-header-wordmark` |
| 20px | leave | `.mbs-close` (an icon-sized ×, not text) |

Also delete the desktop `.reading-body { font-size: 16.5px; ... }` override's font-size line (keep its `line-height: 1.65`), and change `.reading { max-width: 620px; }` to `max-width: 36rem;` (spec: reading measure ≤ 36rem).

- [ ] **Step 3: Heading weights**

Nunito needs explicit weights (defaults look flabby). Add after the `body` rule in `app.css`:

```css
h1,
h2,
h3 {
  font-weight: 800;
  letter-spacing: -0.005em;
}
```

And `.hero-title` gets `font-weight: 800;` added (it currently inherits h1 default via element — it's a class on an h1; the rule above covers it, no change needed if it's an h1 — check `SpaceView.tsx`; if `.hero-title` is not an h1, add the weight to the class).

- [ ] **Step 4: Verify**

```bash
grep -n "font-size" frontend/src/app.css | grep -v "var(--text\|font-size: 16px\|font-size: 20px"
```
Expected: no output (every size is a token except body's rem root and `.mbs-close`).

```bash
cd frontend && bun test && bun run build
```

Visual: dev server; check sidebar (13.8px items readable), a page's reading view, the editor. Nothing should look comically large/small; if a mapping reads wrong, adjust the mapping (stay on the scale — change which token, not the token's value).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/tokens.css frontend/src/app.css
git commit -m "feat: type-scale tokens; migrate all font sizes onto the scale

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Fan-coral mark — `reef.svg`, `ReefMark`, `FrondGlyph`, raster icons

**Files:**
- Modify: `frontend/public/reef.svg`, `frontend/src/components/ReefMark.tsx`
- Regenerate: `frontend/public/reef-icon.png` (and any other raster icon `src/rif/web/static.py` serves — read `static.py:107-155` first and regenerate exactly what exists)
- Create (dev script): `frontend/scripts/render-icons.ts`

**Interfaces:**
- Consumes: `docs/superpowers/specs/assets/2026-08-09-reef-fan-coral.svg` (committed source of truth).
- Produces: `ReefMark({ size?, className? })` unchanged signature, `aria-label="reef"`; `FrondGlyph({ color, size? })` unchanged signature but **`size` is now the rendered height** (width = size × 42/34.5, non-square crop). Callers (`Home.tsx`, later tasks) rely on `fill`-based single-color rendering.

- [ ] **Step 1: Replace the static SVG**

```bash
cp docs/superpowers/specs/assets/2026-08-09-reef-fan-coral.svg frontend/public/reef.svg
```

Then edit `frontend/public/reef.svg`: ensure `aria-label="reef"` (the asset already has it).

- [ ] **Step 2: Rewrite `ReefMark.tsx`**

Replace the file's entire contents with (the `CORAL_D` value is the single `<path d="...">` from `frontend/public/reef.svg` — copy it verbatim from the file you just wrote; it is one long potrace path starting `M1441 2805 c-40 -8...`):

```tsx
/**
 * The reef mark as inline SVG, mirroring `public/reef.svg`'s fan coral —
 * a dome of thick forking branches vectorized from the approved reference
 * (spec: docs/superpowers/specs/2026-08-09-reef-visual-redesign-design.md).
 * The tile fill points at `--mark-tile` so the same asset reads correctly
 * on light and bioluminescent-dark grounds. `reef.svg` itself stays the
 * favicon, which always wants the light tile regardless of theme.
 *
 * The gradient is userSpaceOnUse on the 64-box (base y=50 → top y=16):
 * objectBoundingBox units degenerate on axis-aligned subpaths, and
 * user-space coords are safe for any geometry drawn in this box.
 */

import { useId } from "react";

/** Potrace path of the fan coral, in the traced source's pixel space. */
const CORAL_D = "<PASTE THE d ATTRIBUTE FROM frontend/public/reef.svg VERBATIM>";

/**
 * Transforms mapping the traced path into the 64-box: outer = fit the
 * source's 228px-wide coral to x 12..52 with its base on y=50; inner =
 * potrace's own pt-space flip. Copied from reef.svg — keep in sync.
 */
const OUTER_TRANSFORM = "translate(3.400,13.333) scale(0.17544)";
const INNER_TRANSFORM = "translate(0,314) scale(0.1,-0.1)";

/** Crop that ends exactly at the coral's base (y=50) so the glyph's bottom edge sits on a text baseline. */
const GLYPH_VIEWBOX = "11 15.5 42 34.5";
/** Width/height ratio of {@link GLYPH_VIEWBOX}. */
export const GLYPH_ASPECT = 42 / 34.5;

/** The coral geometry, paint inherited from the parent (`fill` cascades into the paths). */
function CoralPaths() {
  return (
    <g transform={OUTER_TRANSFORM}>
      <g transform={INNER_TRANSFORM}>
        <path d={CORAL_D} />
      </g>
    </g>
  );
}

interface ReefMarkProps {
  /** Rendered width/height in px — the tile viewBox is square. Default 30. */
  size?: number;
  /** Extra class applied to the root `<svg>`, for layout hooks. */
  className?: string;
}

/** The full reef mark: rounded `--mark-tile` tile behind the gradient fan coral. */
export function ReefMark({ size = 30, className }: ReefMarkProps) {
  const gradientId = useId();
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      role="img"
      aria-label="reef"
      className={className}
    >
      <defs>
        <linearGradient
          id={gradientId}
          gradientUnits="userSpaceOnUse"
          x1="0"
          y1="50"
          x2="0"
          y2="16"
        >
          <stop offset="0" stopColor="#0d9488" />
          <stop offset="1" stopColor="#5eead4" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="var(--mark-tile)" />
      <g fill={`url(#${gradientId})`}>
        <CoralPaths />
      </g>
    </svg>
  );
}

interface FrondGlyphProps {
  /** Fill color — a space hue, `var(--accent)`, or `currentColor`. */
  color: string;
  /** Rendered HEIGHT in px (width = height × GLYPH_ASPECT). Default 20. */
  size?: number;
}

/**
 * The coral alone, no tile — single-color glyph for space chips and brand
 * lockups. The viewBox bottom is the coral's base, so in a
 * `align-items: baseline` flex row the coral stands on the text baseline.
 */
export function FrondGlyph({ color, size = 20 }: FrondGlyphProps) {
  return (
    <svg
      viewBox={GLYPH_VIEWBOX}
      width={size * GLYPH_ASPECT}
      height={size}
      preserveAspectRatio="xMidYMax meet"
      aria-hidden="true"
    >
      <g fill={color}>
        <CoralPaths />
      </g>
    </svg>
  );
}
```

- [ ] **Step 3: Regenerate raster icons**

First `Read src/rif/web/static.py` lines 100–160 and list which raster files are served (expected: `frontend/public/reef-icon.png` as `/apple-touch-icon.png`; `/favicon.svg`/`.ico` may point at the SVG). Then:

```bash
cd frontend && bun add -d @resvg/resvg-js
```

Create `frontend/scripts/render-icons.ts`:

```ts
/**
 * Renders public/reef.svg to the raster icon sizes the backend serves.
 * Run: bun run scripts/render-icons.ts
 */
import { Resvg } from "@resvg/resvg-js";

const svg = await Bun.file(new URL("../public/reef.svg", import.meta.url)).text();

for (const { out, size } of [{ out: "../public/reef-icon.png", size: 180 }]) {
  const png = new Resvg(svg, { fitTo: { mode: "width", value: size } }).render().asPng();
  await Bun.write(new URL(out, import.meta.url), png);
  console.log(`${out}: ${png.length} bytes at ${size}px`);
}
```

```bash
cd frontend && bun run scripts/render-icons.ts
```

Adjust the size list to match whatever `static.py` actually serves (apple-touch convention is 180px).

- [ ] **Step 4: Verify**

```bash
cd frontend && bun test && bun run build
```

Visual: dev server → `/app/signed-out` shows the fan-coral tile (ReefMark). Open `http://localhost:3000/app/public/reef.svg` (or the dist copy) — fan coral, gradient visible bottom-to-top. Check the browser tab favicon.

- [ ] **Step 5: Commit**

```bash
git add frontend/public/reef.svg frontend/public/reef-icon.png frontend/src/components/ReefMark.tsx frontend/scripts/render-icons.ts frontend/package.json frontend/bun.lock
git commit -m "feat: fan-coral mark — traced SVG, ReefMark/FrondGlyph geometry, raster icons

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Rename + lockup B brand rows (sidebar & mobile header)

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx:74-77`, `frontend/src/components/AppShell.tsx:96-104`, `frontend/index.html:11`, `frontend/src/app.css` (`.side-brand`, `.app-header-*`)

**Interfaces:**
- Consumes: `FrondGlyph({ color, size })` from Task 3 (height-based size, baseline-bottom viewBox).

- [ ] **Step 1: Sidebar brand → lockup B**

In `Sidebar.tsx`, replace the brand link (lines 74–77) with:

```tsx
      <Link to="/" className="side-brand">
        <FrondGlyph color="var(--accent)" size={15} />
        reef
      </Link>
```

Update the import: `import { FrondGlyph } from "./ReefMark";` (drop `ReefMark` if now unused in this file).

- [ ] **Step 2: Mobile header → lockup B**

In `AppShell.tsx`, replace lines 97–100 with:

```tsx
          <Link to="/" className="app-header-link">
            <FrondGlyph color="var(--accent)" size={17} />
            <span className="app-header-wordmark">reef</span>
          </Link>
```

Update the import to `import { FrondGlyph } from "./ReefMark";` (drop `ReefMark` if unused).

- [ ] **Step 3: CSS — baseline alignment + wordmark weight**

In `app.css`, `.side-brand`: change `align-items: center` → `align-items: baseline`, `font-weight: 700` → `font-weight: 800`, and add `letter-spacing: -0.005em;`. Delete the now-unused `.side-brand-icon` rule and `.app-header-icon` rule.

`.app-header-link`: change `align-items: center` → `align-items: baseline`.

`.app-header-wordmark`: replace with:

```css
.app-header-wordmark {
  font-size: var(--text-lg);
  font-weight: 800;
  letter-spacing: -0.005em;
  color: var(--ink);
}
```

- [ ] **Step 4: Title**

`frontend/index.html:11`: `<title>rif</title>` → `<title>reef</title>`.

- [ ] **Step 5: Verify**

```bash
grep -rn '\brif\b' frontend/src frontend/index.html | grep -v "X-Rif-Csrf"
```
Expected: no user-visible matches (comments mentioning the old name are fine to leave; strings/JSX/aria must be clean).

```bash
cd frontend && bun test && bun run build
```

Visual (dev server, desktop width): sidebar shows the coral standing on the "reef" baseline, weight 800; mobile width (<900px) header shows the same lockup; both themes (toggle OS or add `data-theme="dark"` on `<html>` via devtools).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Sidebar.tsx frontend/src/components/AppShell.tsx frontend/index.html frontend/src/app.css
git commit -m "feat: rename to reef; lockup B brand rows in sidebar and mobile header

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Lockup C on SignedOut

**Files:**
- Modify: `frontend/src/views/SignedOut.tsx`, `frontend/src/app.css` (add `.lockup-c`)

**Interfaces:**
- Consumes: `FrondGlyph` (Task 3).

- [ ] **Step 1: Markup**

Replace `SignedOut.tsx`'s `<ReefMark size={44} />` line with:

```tsx
      <div className="lockup-c" aria-label="reef">
        <FrondGlyph color="var(--accent)" size={26} />
        <span>reef</span>
      </div>
```

Change the import to `import { FrondGlyph } from "../components/ReefMark";`.

- [ ] **Step 2: CSS**

Add to `app.css` next to `.signed-out`:

```css
/* Lockup C — splash-only: coral + wordmark growing from a shared accent "seabed" rule. */
.lockup-c {
  display: inline-flex;
  align-items: baseline;
  gap: 0.28em;
  position: relative;
  font-size: 2rem;
  font-weight: 800;
  letter-spacing: -0.005em;
  color: var(--ink);
}

.lockup-c::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: -0.14em;
  height: 0.09em;
  border-radius: 0.05em;
  background: var(--accent);
}
```

- [ ] **Step 3: Verify**

Dev server → `/app/signed-out`: coral + "reef" on one baseline with the accent seabed rule under both; both themes.

```bash
cd frontend && bun test && bun run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/SignedOut.tsx frontend/src/app.css
git commit -m "feat: lockup C (shared seabed) on the signed-out splash

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Spaces screen — list/tile views with segmented icon picker

**Files:**
- Create: `frontend/src/spacesView.ts`, `frontend/src/spacesView.test.ts`
- Modify: `frontend/src/views/Home.tsx`, `frontend/src/app.css` (add `.spaces-head`, `.segview`, `.space-tile*` rules)

**Interfaces:**
- Produces: `getSpacesView(): "list" | "grid"` and `setSpacesView(view: "list" | "grid"): void` in `spacesView.ts`.
- Consumes: `FrondGlyph`, `spaceColor`, `useMembers`, `AvatarStack` (all existing).

- [ ] **Step 1: Write the failing test**

`frontend/src/spacesView.test.ts`:

```ts
import { beforeEach, expect, test } from "bun:test";

import { getSpacesView, setSpacesView } from "./spacesView";

beforeEach(() => {
  localStorage.clear();
});

test("defaults to list", () => {
  expect(getSpacesView()).toBe("list");
});

test("round-trips grid", () => {
  setSpacesView("grid");
  expect(getSpacesView()).toBe("grid");
});

test("ignores junk stored values", () => {
  localStorage.setItem("reef.spacesView", "carousel");
  expect(getSpacesView()).toBe("list");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && bun test spacesView`
Expected: FAIL — `Cannot find module './spacesView'`.

- [ ] **Step 3: Implement**

`frontend/src/spacesView.ts`:

```ts
/**
 * The Spaces screen's persisted view preference (spec: "Main screen").
 * localStorage-backed so it survives reloads without a server round-trip;
 * unknown/absent values fall back to the default rather than throwing.
 */

export type SpacesView = "list" | "grid";

const KEY = "reef.spacesView";

/** Read the persisted view, defaulting to `"list"` for absent or junk values. */
export function getSpacesView(): SpacesView {
  try {
    const raw = localStorage.getItem(KEY);
    return raw === "grid" ? "grid" : "list";
  } catch {
    return "list";
  }
}

/** Persist the chosen view. Storage failures (private mode) are non-fatal. */
export function setSpacesView(view: SpacesView): void {
  try {
    localStorage.setItem(KEY, view);
  } catch {
    // Preference simply won't stick — acceptable.
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && bun test spacesView`
Expected: 3 pass. (jsdom via `testSetup.ts` provides `localStorage`; if not, add `import "./testSetup";` per the existing test files' pattern — check how `markdown.test.ts` does it and copy that.)

- [ ] **Step 5: Home.tsx — picker + tile view**

Rewrite `Home.tsx`'s default export and add a `SpaceTile` sibling to `SpaceCard` (keep `SpaceCard` as-is):

```tsx
/** One space as a grid tile: coral glyph in a circular hue "pool", alias, subline. */
function SpaceTile({ space }: { space: SpaceIndex }) {
  const isPersonal = space.alias === "personal";
  const hue = spaceColor(space.alias);
  const { members } = useMembers(space.alias);
  const pageCount = space.pages.length;

  return (
    <Link
      to={`/s/${space.alias}`}
      className="card space-tile"
      style={{ "--hue-base": hue.base, "--hue-light": hue.light } as CSSProperties}
    >
      <span className="space-tile-pool" aria-hidden="true">
        <FrondGlyph color={hue.light} size={24} />
      </span>
      <span className="space-card-alias">{isPersonal ? "Personal" : space.alias}</span>
      <span className="space-card-sub muted">
        {pageCount} page{pageCount === 1 ? "" : "s"}
        {isPersonal ? " · only you" : ""}
      </span>
      {!isPersonal && members && (
        <AvatarStack
          names={members.members.map((member) => member.display_name)}
          size="sm"
          ariaLabel={`Members of ${space.alias}`}
        />
      )}
    </Link>
  );
}

export default function Home() {
  const { index, error } = useIndex();
  const spaces = index?.spaces ?? null;
  const [view, setView] = useState<SpacesView>(getSpacesView);

  function pick(next: SpacesView) {
    setView(next);
    setSpacesView(next);
  }

  return (
    <div>
      <div className="spaces-head">
        <h1>Spaces</h1>
        <div className="segview" role="tablist" aria-label="View">
          <button
            type="button"
            role="tab"
            className={`seg ${view === "list" ? "seg-active" : ""}`}
            aria-selected={view === "list"}
            aria-label="List view"
            onClick={() => pick("list")}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M2 4h12M2 8h12M2 12h12"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
                fill="none"
              />
            </svg>
          </button>
          <button
            type="button"
            role="tab"
            className={`seg ${view === "grid" ? "seg-active" : ""}`}
            aria-selected={view === "grid"}
            aria-label="Tile view"
            onClick={() => pick("grid")}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
              <g fill="currentColor">
                <rect x="2" y="2" width="5.2" height="5.2" rx="1.4" />
                <rect x="8.8" y="2" width="5.2" height="5.2" rx="1.4" />
                <rect x="2" y="8.8" width="5.2" height="5.2" rx="1.4" />
                <rect x="8.8" y="8.8" width="5.2" height="5.2" rx="1.4" />
              </g>
            </svg>
          </button>
        </div>
      </div>
      {error && <div className="notice">{error}</div>}
      {!error && spaces === null && <p className="muted">Loading…</p>}
      {spaces !== null && spaces.length === 0 && (
        <p className="muted">No spaces yet.</p>
      )}
      {view === "list" ? (
        <ul className="card-list">
          {spaces?.map((space) => (
            <li key={space.alias}>
              <SpaceCard space={space} />
            </li>
          ))}
        </ul>
      ) : (
        <ul className="tile-grid">
          {spaces?.map((space) => (
            <li key={space.alias}>
              <SpaceTile space={space} />
            </li>
          ))}
          <li>
            <Link to="/spaces/new" className="card space-tile space-tile-new">
              <span className="space-tile-plus" aria-hidden="true">+</span>
              New space
            </Link>
          </li>
        </ul>
      )}
      {view === "list" && (
        <p>
          <Link to="/spaces/new" className="button">
            New space
          </Link>
        </p>
      )}
    </div>
  );
}
```

Add imports at the top of the file: `import { useState } from "react";` and `import { getSpacesView, setSpacesView, type SpacesView } from "../spacesView";`.

- [ ] **Step 6: CSS**

Add to `app.css` after the `.space-card-sub` rule:

```css
/* Spaces heading row: title left, view picker right (spec: "Main screen"). */
.spaces-head {
  display: flex;
  align-items: center;
}

.spaces-head h1 {
  margin-right: auto;
}

/* Segmented icon picker — pill track, raised active segment. */
.segview {
  display: inline-flex;
  gap: 2px;
  padding: 3px;
  background: var(--panel);
  border: 1px solid var(--hairline);
  border-radius: 999px;
}

.seg {
  min-height: auto;
  min-width: auto;
  width: 34px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: none;
  color: var(--muted);
  display: grid;
  place-items: center;
  cursor: pointer;
}

.seg-active {
  background: var(--field);
  color: var(--accent);
  box-shadow: 0 1px 3px rgb(10 30 40 / 0.15);
}

.seg:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* Tile view: 2-up grid of habitat tiles. */
.tile-grid {
  list-style: none;
  margin: 1rem 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.75rem;
}

.space-tile {
  align-items: flex-start;
  gap: 0.4rem;
  min-height: 7.5rem;
}

.space-tile-pool {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--hue-base) 12%, white);
}

.space-tile-new {
  border-style: dashed;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  font-weight: 600;
  font-size: var(--text-base);
}

.space-tile-plus {
  font-size: 1.5rem;
  color: var(--accent);
  line-height: 1;
}
```

And in BOTH dark blocks at the bottom of `app.css` (the `@media (prefers-color-scheme: dark)` one and the `:root[data-theme="dark"]` one — they must stay in sync), extend the existing `.space-card-chip` rule to also cover the pool:

```css
    .space-card-chip,
    .space-tile-pool {
      background: color-mix(in srgb, var(--hue-base) 25%, #0d1a20);
    }
```

- [ ] **Step 7: Verify**

```bash
cd frontend && bun test && bun run build
```

Visual (dev server, signed in with the backend running — see Final Verification for how): Spaces screen shows the picker beside the heading; toggling flips list ↔ 2-col tiles; reload keeps the choice; the dashed "+ New space" tile appears only in grid view; both themes.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/spacesView.ts frontend/src/spacesView.test.ts frontend/src/views/Home.tsx frontend/src/app.css
git commit -m "feat: Spaces list/tile views with persisted segmented picker

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Full-app verification pass

**Files:** none (fixes only if issues found)

- [ ] **Step 1: Static gates**

```bash
grep -rn "Georgia\|--serif" frontend/src/ && echo LEAK || echo clean
grep -rn '"rif"\|>rif<' frontend/src frontend/index.html && echo LEAK || echo clean
cd frontend && bun test && bun run build
```

- [ ] **Step 2: Run the real app**

Consult `README.md` for the backend run steps (Postgres via `docker-compose.yml`, then the Python server; the frontend dev server proxies `/api` → :8000). If the backend can't run locally, verify what's verifiable static-only (`/app/signed-out`, fonts, favicon) and say so explicitly in the report — do not claim full verification.

- [ ] **Step 3: Playwright screenshot sweep**

With the app running and signed in, screenshot in BOTH themes (set `data-theme="dark"` on `<html>` for the dark pass): Spaces (list + grid), a space's page list, a page reading view, the editor, `/app/signed-out`. Compare against the approved artifact mockup (Nunito everywhere, fan coral, lockup B brand rows, lockup C splash, no serif). Fix regressions found; re-run gates.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "fix: visual-verification follow-ups for the reef redesign

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
