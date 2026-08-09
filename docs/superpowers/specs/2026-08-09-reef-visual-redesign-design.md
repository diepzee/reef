# reef — visual redesign (rename, mark, typography)

**Date:** 2026-08-09
**Status:** approved via interactive mockup rounds (artifact "reef · type audition")
**Supersedes:** the identity portions of `2026-08-08-web-frontend-v2-design.md` (its layout,
avatar, and color decisions stand; its mark, wordmark, and serif-reading decisions are replaced)

## Context

The app ships under the name "rif" with the v2 "hybrid" design: good color tokens, but the
system-font stack, no type scale, and a mark/wordmark pairing that reads bland. The product is
renamed **reef** (domain: reefwith.me). This spec locks the new identity, chosen over several
interactive mockup rounds: a traced fan-coral mark, Nunito as the single typeface, baseline
lockups, and the retirement of the Georgia serif reading view. The direction settled on
**rounded-friendly**: the mark and the type share the same soft DNA.

## Decisions (all approved)

| Axis | Decision |
|------|----------|
| Name | **reef**, lowercase, everywhere in UI |
| Mark | **Fan coral** — dome of thick forking branches, vectorized from Wouter's reference image (color-threshold mask → potrace). Not hand-approximated. |
| Mark treatment | Seafoam gradient `#0d9488 → #5eead4`, bottom-to-top, on the existing `--mark-tile` tile. Gradient MUST be `gradientUnits="userSpaceOnUse"` (`x1=0 y1=50 x2=0 y2=16`) — objectBoundingBox degenerates on axis-aligned subpaths. |
| Everyday lockup (B) | Tile-less coral glyph + wordmark, **bottoms aligned**: the coral's flat base sits exactly on the text baseline. Used in sidebar brand row and mobile header. |
| Splash lockup (C) | Lockup B plus an accent "seabed" rule extending under the whole word (baseline underline, ~0.09em thick, 0.14em below baseline). Used on SignedOut / sign-in / empty states only. |
| Typeface | **Nunito** (variable, self-hosted latin woff2). One family everywhere — no pairing, no serif. Weights: 800 wordmark/display, 700 headings, 600 UI labels/buttons, 400 body. |
| Reading view | All-Nunito. Georgia/`--serif` retired entirely. |
| Colors/themes | Unchanged: v2 seafoam light + bioluminescent night-dive dark (`tokens.css` palette stays). |

## Assets

The production mark is committed at
`docs/superpowers/specs/assets/2026-08-09-reef-fan-coral.svg` (64-box, tile + gradient +
traced path). It replaces the seven-frond colony everywhere:

- `frontend/public/reef.svg` — replace content with the new mark; keep `aria-label` but change
  it from `"rif"` to `"reef"`. The tile `fill` stays `#e7f9f4` in the static file (favicon
  context); the in-app component uses `var(--mark-tile)`.
- `frontend/src/components/ReefMark.tsx` — same geometry inline; `ReefMark` (tile version,
  `--mark-tile` fill) and `FrondGlyph` (tile-less, single-`currentColor` fill for space cards
  and lockups). FrondGlyph's crop viewBox must end at y=50 (the coral's base) so
  baseline-alignment works without fudge factors.
- `frontend/public/reef-icon.png`, apple-touch icon — regenerate from the new SVG.
- At 16px the channels get dense but the silhouette holds; a simplified small-size cut is a
  possible later refinement, not in scope now.

## Typography

- Self-host Nunito: latin-subset variable woff2 (≈39 KB) under `frontend/public/fonts/`,
  loaded via `@font-face` (`font-weight: 200 1000; font-display: swap`) in CSS — no Google
  Fonts runtime dependency. Bun's HTML bundler serves `public/` assets as-is.
- `--font-sans` becomes `"Nunito", -apple-system, …` and **moves from `app.css` into
  `tokens.css`** (both dark blocks unaffected — font tokens are theme-invariant, define once
  in `:root`).
- Remove `--serif` from `tokens.css`; remove serif `font-family` from `.app-tagline`(if any),
  `.reading-title`, `.reading-body` in `app.css`.
- Introduce a **type scale** as tokens (in `tokens.css` `:root`): `--text-xs: 0.76rem`,
  `--text-sm: 0.84rem`, `--text-base: 0.95rem`, `--text-md: 1.02rem`, `--text-lg: 1.25rem`,
  `--text-xl: 1.8rem`, `--text-2xl: 2.1rem`. Migrate `app.css` font-size literals to the
  nearest step (judgment calls allowed; the scale is the point, not pixel-perfect parity).
- Wordmark: lowercase `reef`, weight 800, letter-spacing `-0.005em`.
- Reading view: body 1.02rem/1.65, measure ≤ 36rem, title weight 800.

## Rename (UI-visible only)

- `frontend/src/components/AppShell.tsx:99` — wordmark string `rif` → `reef`.
- `frontend/index.html` — `<title>reef</title>`.
- `ReefMark.tsx` / `reef.svg` — `aria-label="reef"`.
- **Out of scope:** backend/package names (`pyproject.toml`, `src/rif/`, DB names,
  `X-Rif-Csrf` header, Docker). These are invisible to users and renaming them risks breaking
  deploys for zero UX gain. Revisit only if/when the repo itself is renamed.

## Lockup implementation notes

- Sidebar brand row and mobile header use lockup B: `FrondGlyph` at ~0.92em of the wordmark's
  font-size, colored `var(--accent)` (light) — it inherits the brighter accent in dark mode
  automatically via `currentColor`.
- Baseline mechanics: render the glyph as an inline SVG whose viewBox bottom equals the
  coral's base (y=50); in a flex row with `align-items: baseline`, the SVG's bottom edge sits
  on the text baseline. No manual nudges.
- SignedOut page gets lockup C (the seabed rule) at display size.

## Main screen (Spaces)

- The noun stays **Spaces** — the theme lives in the visuals, not the vocabulary.
- The Spaces screen ships **two switchable views** (approved as mockups V1 + V2):
  - **List** — today's structure: hue-striped cards (space gradient stripe, coral glyph in
    space hue, name, meta, whobar avatars).
  - **Tiles** — a 2-column grid: each space is a tile with its coral glyph in a circular
    hue-tinted "pool", name, meta; the last tile is a dashed-border "+ New space".
- A **segmented icon picker** sits right-aligned beside the "Spaces" heading: pill-shaped
  control, two icon segments (list = three horizontal lines, grid = 2×2 rounded squares),
  active segment gets a raised `--field` background and accent-colored icon, so the selected
  view is visible at a glance.
- The chosen view persists per user (`localStorage`, key `reef.spacesView`, values
  `"list" | "grid"`; default `list`).
- The V3 "Cove" branded-header direction was reviewed and not chosen.

## Out of scope for this spec

- Spacing/radius/shadow token system and the full shell/view restyle pass (next spec — this
  one is identity + type + rename so it can ship as one reviewable unit).
- Dark-block dedup in `tokens.css` (keep both blocks in sync as today).
- Marketing/landing pages for reefwith.me.

## Verification

- `cd frontend && bun test` (pure-function tests; should be untouched).
- `bun run dev` (or the deployed preview) → Playwright screenshots of: Spaces list, a reading
  page, the editor, SignedOut — in both themes (`data-theme="dark"` override and OS dark) —
  compare against the approved artifact mockup (Nunito + fan coral + lockup B everywhere,
  lockup C on SignedOut, no Georgia anywhere).
- Favicon check: browser tab shows the fan coral at 16px; iOS home-screen icon renders from
  the regenerated PNG.
- Grep gates: no `Georgia` in `frontend/src`; no user-visible `rif` string in
  `frontend/src`/`index.html` (the `X-Rif-Csrf` header constant is exempt).
