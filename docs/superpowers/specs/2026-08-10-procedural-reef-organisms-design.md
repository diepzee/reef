# Procedural reef organisms + reef/cove naming

Date: 2026-08-10
Status: approved direction, pending spec review

## Goal

Every space grows its own **unique, procedurally generated reef organism** —
not one of six fixed drawings. The visual style shifts to the chunky filled
silhouettes of the approved reference sheet (12 organisms, flat coral-red
sample), and that style takes over the whole brand: the traced fan-coral mark
is redrawn as a chunky silhouette too.

In UI copy, "Spaces" disappears: the collection is **the reef**, an individual
space is a **cove**. Backend/API/MCP vocabulary (`Space`, `create_space`,
`/api/spaces/...`, aliases) is untouched.

## Non-goals

- No backend or API renames.
- No change to `spaceColor` — the 7-hue palette and its char-sum hash stay
  exactly as they are (existing spaces keep their hue).
- No animation. Static silhouettes only.

## 1. The generator

### Families

Eleven hashed body-plan families, drawn from the reference sheet:

| family | body plan | anchor |
|---|---|---|
| `sunAnemone` | radial ring of fat petals around an open center | radial |
| `tubes` | 3–5 tube sponges with elliptical mouth holes | grounded |
| `staghorn` | forking antler branches | grounded |
| `brain` | dome with meandering groove cutouts | grounded |
| `flower` | 5–7 fat overlapping lobes around a hole | radial |
| `scallop` | fan of rays on a stem foot | grounded |
| `spiral` | swirling one-direction curled tentacles | radial |
| `bubbles` | cluster of tangent rounded blobs | grounded |
| `seagrass` | tapered wavy blades | grounded |
| `shell` | ribbed clam fan | grounded |
| `nudibranch` | asymmetric slug with rhinophores and dot pattern | grounded |

The reference's branching fan/tree coral is **not** in the hashed pool — that
body plan becomes the redrawn brand mark, pinned to `personal` (same
discipline as its seafoam hue). 11 families × 7 hues = 77 combinations before
continuous parameter variation, and 11 vs 7 are coprime so family and hue
assignments cycle independently.

### Hash → seed → parameters

- `fnv1a(alias)` → 32-bit seed. (An actual hash this time, not a char sum —
  char sums collide on anagrams. Organism assignments for existing spaces
  will shuffle once; accepted, this is a redesign.)
- `family = FAMILIES[seed % 11]` for every alias except `personal`.
- `mulberry32(seed)` PRNG drives the parameters. Each family draws its
  parameters **in a fixed documented order** from the PRNG — the whole
  pipeline is pure and deterministic: same alias, same organism, forever,
  on every platform.
- Parameters are things like: petal/blade/tube/branch count, lengths,
  fatness, sway, lean, hole size, rotation phase, dot placement. Each has a
  hand-tuned min–max range chosen so every draw keeps roughly equal visual
  mass ("weight normalization" — this is tuning work, the core of the task).

### Geometry

- Shared square 64-box, square rendering (the old per-glyph aspect crop
  goes away — `SpaceGlyph` renders `size × size`).
- **Grounded** families stand on a baseline near the box bottom (y = 54);
  **radial** families center on (32, 32).
- Filled silhouettes in `currentColor`; holes (anemone center, brain
  grooves, tube mouths, nudibranch dots) via `fillRule="evenodd"` subpaths.
  Thick round-capped strokes are still allowed where they read as filled
  mass at 20 px (staghorn forks), matching the reference's weight.
- A small shared helper kit builds the paths: teardrop petal, tapered
  blade, capsule/tube, ring-repeat with rotation, mirrored pair. No
  geometry libraries, no dependencies.

### Code shape

- `frontend/src/components/organisms.ts` — hash, PRNG, parameter drawing,
  per-family path generators (pure: `alias → { family, paths }` as SVG path
  data strings, no JSX). Unit-testable without rendering.
- `frontend/src/components/spaceGlyph.tsx` — thin `SpaceGlyph` component
  over `organisms.ts`; keeps its current props (`alias`, `color`, `size`).
  `ORGANISMS`/`organismFor` are replaced by the new module's exports.

## 2. Brand mark redraw

The potrace-traced fan coral is replaced by a hand-authored chunky
silhouette fan coral (branching dome with trunk, reference sheet style):

- `ReefMark` — same tile + seafoam gradient treatment, new geometry. The
  transform stack (`OUTER_TRANSFORM`/`INNER_TRANSFORM`, trace-space path)
  goes away; the new path is authored directly in the 64-box.
- `FrondGlyph` — same component contract, new path, square viewBox.
- `public/reef.svg` — updated to the same geometry (stays the favicon).
- Raster icons regenerated via the existing `bun run scripts/render-icons.ts`
  (no changes to `src/rif/web/static.py`).
- `personal` renders this same fan coral as its glyph, as today.

## 3. Naming sweep (UI copy only)

- Standalone label "Spaces" → **"Reef"**: `Home.tsx` `<h1>`, `Sidebar.tsx`
  side-label. Page titles/`pageMeta` follow.
- Sentence-level "space" → **"cove"** in all user-visible copy: members
  sheet, invite/leave/manage strings, aria-labels, empty states, error
  toasts. `"People in {alias}"`-style interpolations keep the alias.
- Identifiers, routes (`/s/`), API paths, comments, and docs describing the
  backend concept keep "space".

## 4. Dev gallery (tuning tool)

A dev-only route (`/gallery`, gated on `import.meta.env.DEV`) rendering a
grid: every family × a spread of seeds × the hue palette, at both 20 px and
64 px. This is where parameter ranges get eyeballed and tuned — and it's
what we screenshot (Playwright) to review the family designs together.
Ships in the first implementation step, before family tuning.

## 5. Testing

- `organisms.test.ts`: determinism (same alias → identical path data);
  `personal` pins to the fan coral; family distribution over a corpus of
  realistic aliases hits all 11 families; generated path data is valid
  (starts with `M`, contains no `NaN`); parameters stay in range across a
  seed sweep.
- Update `spaceGlyph.test.ts` for the new exports; drop the old
  organism-name expectations.
- Existing static/icon backend tests keep passing after regeneration.
- Visual: Playwright screenshots of `/gallery` for human review (not
  snapshot-asserted — silhouettes will be tuned iteratively).

## 6. Marketing page (`docs/how-it-works.html`) — follow-up phase

After the app work lands (and a rebase onto latest `main`): bring the
marketing page onto the new visual language. It still runs the pre-redesign
identity (Seravek font stack, old teal palette, no reef mark). Scope:

- Nunito + the app's type scale and current palette tokens.
- The new chunky fan-coral mark in the header/lockup; procedural organisms
  used as section accents where they genuinely help (sparingly — it's an
  explainer, not an aquarium).
- "Spaces" → reef/cove copy sweep here too.
- Refined iteratively with Playwright screenshots (light + dark, mobile +
  desktop), reviewed critically — spacing, hierarchy, contrast — not just
  reskinned.

## Open questions

None — approach A (hand-written parametric families) approved 2026-08-10.
