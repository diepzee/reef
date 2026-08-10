# Procedural Reef Organisms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every space renders a unique, deterministic, procedurally generated reef organism (11 parametric families + fan coral pinned to `personal`); the brand mark becomes a chunky silhouette; UI copy drops "Spaces" for reef/cove; finally the marketing page adopts the new language.

**Architecture:** A pure module `frontend/src/components/organisms.ts` maps `alias → fnv1a seed → mulberry32 PRNG → parameters → SVG path data`. `SpaceGlyph` becomes a thin renderer over it (square 64-box). `ReefMark`/`FrondGlyph` get a hand-authored chunky fan coral that is also the `personal` organism. A dev-only `/gallery` route is the tuning surface.

**Tech Stack:** React 19, TypeScript, `bun test` (run from `frontend/`), resvg for icons, Playwright MCP for visual review. **No new dependencies.**

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-10-procedural-reef-organisms-design.md`.
- Determinism: same alias → byte-identical path data, everywhere. No `Math.random`, no `Date`.
- `personal` always renders the fan coral; `spaceColor` is untouched.
- Backend/API/routes keep "space"; only user-visible copy changes to Reef/cove.
- All coordinates in a 64-box; grounded families baseline y=54; radial center (32,32); numbers rounded to 2 decimals via the shared `r2` helper.
- File header comments follow the existing convention (see `spaceGlyph.tsx`): explain the *why*, reference the spec.
- Run tests from `frontend/`: `bun test` (all) or `bun test <file>`.

---

### Task 1: Core module — hash, PRNG, path helpers

**Files:**
- Create: `frontend/src/components/organisms.ts`
- Test: `frontend/src/components/organisms.test.ts`

**Interfaces (produced, used by every later task):**

```ts
export type Anchor = "grounded" | "radial";
export interface OrganismPath { d: string; stroke?: number; evenodd?: boolean }
export interface Organism { family: Family; paths: OrganismPath[] }
export function fnv1a(str: string): number;          // 32-bit unsigned
export function mulberry32(seed: number): () => number; // [0,1)
// internal helpers: r2, lerp, int, lobe, blade, tube, circle, slot
```

- [ ] **Step 1: Write failing tests**

```ts
// frontend/src/components/organisms.test.ts
import { describe, expect, test } from "bun:test";
import { fnv1a, mulberry32 } from "./organisms";

describe("fnv1a", () => {
  test("is deterministic and 32-bit unsigned", () => {
    expect(fnv1a("roadtrip")).toEqual(fnv1a("roadtrip"));
    expect(fnv1a("roadtrip")).toBeGreaterThanOrEqual(0);
    expect(fnv1a("roadtrip")).toBeLessThanOrEqual(0xffffffff);
  });
  test("separates anagrams (unlike char-sum)", () => {
    expect(fnv1a("stop")).not.toEqual(fnv1a("pots"));
  });
});

describe("mulberry32", () => {
  test("same seed → same sequence, in [0,1)", () => {
    const a = mulberry32(42), b = mulberry32(42);
    for (let i = 0; i < 100; i++) {
      const v = a();
      expect(v).toEqual(b());
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });
  test("different seeds diverge", () => {
    expect(mulberry32(1)()).not.toEqual(mulberry32(2)());
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd frontend && bun test src/components/organisms.test.ts` → FAIL (module missing).

- [ ] **Step 3: Implement the core**

```ts
// frontend/src/components/organisms.ts  (header comment: procedural organisms, spec ref)

export type Anchor = "grounded" | "radial";
export interface OrganismPath { d: string; stroke?: number; evenodd?: boolean }

/** FNV-1a 32-bit hash — unlike a char sum, anagram aliases get distinct seeds. */
export function fnv1a(str: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/** Tiny deterministic PRNG; each organism draws its parameters from one stream. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Rng = () => number;
const r2 = (n: number): string => String(Math.round(n * 100) / 100);
const lerp = (rng: Rng, min: number, max: number) => min + rng() * (max - min);
const int = (rng: Rng, min: number, max: number) => Math.floor(lerp(rng, min, max + 1));

/** Fat rounded petal pointing along `theta` from (x, y). */
function lobe(x: number, y: number, theta: number, len: number, w: number): string {
  const dx = Math.cos(theta), dy = Math.sin(theta);
  const px = -dy, py = dx;
  const bw = w * 0.4;
  const mx = x + dx * len * 0.62, my = y + dy * len * 0.62;
  const tx = x + dx * len, ty = y + dy * len;
  return (
    `M${r2(x + px * bw)} ${r2(y + py * bw)}` +
    `C${r2(mx + px * w)} ${r2(my + py * w)} ${r2(tx + px * w * 0.55)} ${r2(ty + py * w * 0.55)} ${r2(tx)} ${r2(ty)}` +
    `C${r2(tx - px * w * 0.55)} ${r2(ty - py * w * 0.55)} ${r2(mx - px * w)} ${r2(my - py * w)} ${r2(x - px * bw)} ${r2(y - py * bw)}Z`
  );
}

/** Tapered seagrass blade rooted at (x, y0), tip swayed sideways. */
function blade(x: number, y0: number, h: number, sway: number, w: number): string {
  const tx = x + sway, ty = y0 - h;
  return (
    `M${r2(x - w)} ${r2(y0)}` +
    `C${r2(x - w * 0.9)} ${r2(y0 - h * 0.45)} ${r2(tx - w * 0.35)} ${r2(ty + h * 0.25)} ${r2(tx)} ${r2(ty)}` +
    `C${r2(tx + w * 0.15)} ${r2(ty + h * 0.3)} ${r2(x + w * 0.95)} ${r2(y0 - h * 0.4)} ${r2(x + w)} ${r2(y0)}Z`
  );
}

/** Leaning tube sponge with an elliptical mouth hole (render with evenodd). */
function tube(x: number, y0: number, h: number, w: number, lean: number): string {
  const topX = x + lean, topY = y0 - h;
  const rx = w / 2, ry = rx * 0.45;
  return (
    `M${r2(x - rx * 1.12)} ${r2(y0)}L${r2(topX - rx)} ${r2(topY)}` +
    `A${r2(rx)} ${r2(ry)} 0 0 1 ${r2(topX + rx)} ${r2(topY)}` +
    `L${r2(x + rx * 1.12)} ${r2(y0)}Z` +
    ellipse(topX, topY, rx * 0.55, ry * 0.55)
  );
}

/** Full-circle subpath (dot, bubble, or evenodd hole). */
function circle(cx: number, cy: number, rad: number): string {
  return ellipse(cx, cy, rad, rad);
}

function ellipse(cx: number, cy: number, rx: number, ry: number): string {
  return (
    `M${r2(cx - rx)} ${r2(cy)}` +
    `A${r2(rx)} ${r2(ry)} 0 1 0 ${r2(cx + rx)} ${r2(cy)}` +
    `A${r2(rx)} ${r2(ry)} 0 1 0 ${r2(cx - rx)} ${r2(cy)}Z`
  );
}

/** Rotated capsule subpath — groove/rib cutout for evenodd shapes. */
function slot(x: number, y: number, len: number, hw: number, ang: number): string {
  const dx = Math.cos(ang), dy = Math.sin(ang);
  const px = -dy * hw, py = dx * hw;
  const ax = x - (dx * len) / 2, ay = y - (dy * len) / 2;
  const bx = x + (dx * len) / 2, by = y + (dy * len) / 2;
  return (
    `M${r2(ax + px)} ${r2(ay + py)}A${r2(hw)} ${r2(hw)} 0 0 1 ${r2(ax - px)} ${r2(ay - py)}` +
    `L${r2(bx - px)} ${r2(by - py)}A${r2(hw)} ${r2(hw)} 0 0 1 ${r2(bx + px)} ${r2(by + py)}Z`
  );
}
```

(`lobe`/`blade`/`tube`/`circle`/`slot` are module-private; exported pieces are only what the tests and later tasks need. Add `export` to helpers only if a later task's file imports them — they all live in this same module, so none should.)

- [ ] **Step 4: Run tests** — `bun test src/components/organisms.test.ts` → PASS. (Unused-helper TS warnings are fine until Task 2 uses them.)

- [ ] **Step 5: Commit** — `git add frontend/src/components/organisms.{ts,test.ts} && git commit -m "feat: organism core — fnv1a, mulberry32, silhouette path helpers"`

---

### Task 2: Radial families — sunAnemone, flower, spiral

**Files:**
- Modify: `frontend/src/components/organisms.ts`
- Test: `frontend/src/components/organisms.test.ts`

**Interfaces (produced):**

```ts
export const FAMILIES: readonly ["sunAnemone","tubes","staghorn","brain","flower","scallop","spiral","bubbles","seagrass","shell","nudibranch"];
export type Family = (typeof FAMILIES)[number] | "coral";
export interface Organism { family: Family; anchor: Anchor; paths: OrganismPath[] }
export function generateFamily(family: Exclude<Family, "coral">, seed: number): Organism;
```

- [ ] **Step 1: Write failing tests**

```ts
// append to organisms.test.ts
import { FAMILIES, generateFamily } from "./organisms";

describe("generateFamily", () => {
  test("radial families are deterministic and emit valid paths", () => {
    for (const fam of ["sunAnemone", "flower", "spiral"] as const) {
      for (let seed = 1; seed <= 50; seed++) {
        const a = generateFamily(fam, seed), b = generateFamily(fam, seed);
        expect(a).toEqual(b);
        expect(a.anchor).toEqual("radial");
        expect(a.paths.length).toBeGreaterThan(0);
        for (const p of a.paths) {
          expect(p.d.startsWith("M")).toBeTrue();
          expect(p.d).not.toContain("NaN");
          expect(p.d).not.toContain("undefined");
        }
      }
    }
  });
});
```

- [ ] **Step 2: Run to verify failure** — FAIL (`generateFamily` not exported).

- [ ] **Step 3: Implement**

```ts
export const FAMILIES = [
  "sunAnemone", "tubes", "staghorn", "brain", "flower", "scallop",
  "spiral", "bubbles", "seagrass", "shell", "nudibranch",
] as const;
export type Family = (typeof FAMILIES)[number] | "coral";
export interface Organism { family: Family; anchor: Anchor; paths: OrganismPath[] }

/** Ring of fat petals around an open annulus center. */
function sunAnemone(rng: Rng): OrganismPath[] {
  const n = int(rng, 10, 14);
  const inner = lerp(rng, 6, 8);
  const len = lerp(rng, 11, 14.5);
  const w = lerp(rng, 3.4, 4.6);
  const phase = rng() * Math.PI * 2;
  let petals = "";
  for (let i = 0; i < n; i++) {
    const th = phase + (i / n) * Math.PI * 2;
    petals += lobe(32 + Math.cos(th) * inner, 32 + Math.sin(th) * inner, th, len, w);
  }
  const hole = lerp(rng, 2.6, 4);
  return [
    { d: petals },
    { d: circle(32, 32, inner + 1.5) + circle(32, 32, hole), evenodd: true },
  ];
}

/** 5–7 fat overlapping flower lobes with a pierced center disc. */
function flower(rng: Rng): OrganismPath[] {
  const n = int(rng, 5, 7);
  const len = lerp(rng, 14, 17);
  const w = lerp(rng, 7.5, 10) - n * 0.35;
  const phase = rng() * Math.PI * 2;
  let petals = "";
  for (let i = 0; i < n; i++) {
    const th = phase + (i / n) * Math.PI * 2;
    petals += lobe(32 + Math.cos(th) * 3, 32 + Math.sin(th) * 3, th, len, w);
  }
  const hole = lerp(rng, 2.5, 3.6);
  return [{ d: petals }, { d: circle(32, 32, 6) + circle(32, 32, hole), evenodd: true }];
}

/** Swirl of one-direction curled tentacle strokes. */
function spiral(rng: Rng): OrganismPath[] {
  const n = int(rng, 11, 15);
  const inner = lerp(rng, 3, 4.5);
  const outer = lerp(rng, 13, 16);
  const curl = lerp(rng, 0.9, 1.4) * (rng() < 0.5 ? -1 : 1);
  const phase = rng() * Math.PI * 2;
  let d = "";
  for (let i = 0; i < n; i++) {
    const th = phase + (i / n) * Math.PI * 2;
    const thMid = th + curl * 0.45, thEnd = th + curl;
    const mid = inner + (outer - inner) * 0.55;
    d +=
      `M${r2(32 + Math.cos(th) * inner)} ${r2(32 + Math.sin(th) * inner)}` +
      `Q${r2(32 + Math.cos(thMid) * mid)} ${r2(32 + Math.sin(thMid) * mid)}` +
      ` ${r2(32 + Math.cos(thEnd) * outer)} ${r2(32 + Math.sin(thEnd) * outer)}`;
  }
  return [{ d, stroke: lerp(rng, 2.8, 3.4) }];
}

const RADIAL: ReadonlySet<string> = new Set(["sunAnemone", "flower", "spiral"]);

type Generator = (rng: Rng) => OrganismPath[];
const GENERATORS: Partial<Record<Family, Generator>> = { sunAnemone, flower, spiral };

/** Grow one family member from a seed — the gallery's and organismFor's shared entry. */
export function generateFamily(family: Exclude<Family, "coral">, seed: number): Organism {
  const gen = GENERATORS[family];
  if (!gen) throw new Error(`no generator for family: ${family}`);
  return {
    family,
    anchor: RADIAL.has(family) ? "radial" : "grounded",
    paths: gen(mulberry32(seed)),
  };
}
```

- [ ] **Step 4: Run tests** — PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat: radial organism families — sun anemone, flower polyp, spiral"`

---

### Task 3: Grounded families A — tubes, seagrass, bubbles, staghorn

**Files:** same two files.

- [ ] **Step 1: Extend the determinism/validity test's family list** to `["sunAnemone","flower","spiral","tubes","seagrass","bubbles","staghorn"]`, asserting `anchor` is `"radial"` for the first three and `"grounded"` for the rest (replace the hardcoded radial assertion with a lookup):

```ts
const EXPECTED_ANCHOR: Record<string, string> = {
  sunAnemone: "radial", flower: "radial", spiral: "radial",
  tubes: "grounded", seagrass: "grounded", bubbles: "grounded", staghorn: "grounded",
};
// in the loop: expect(a.anchor).toEqual(EXPECTED_ANCHOR[fam]);
```

- [ ] **Step 2: Run** — FAIL (no generators for new families).

- [ ] **Step 3: Implement** (add to `GENERATORS`; grounded baseline is y=54):

```ts
/** 3–5 leaning tube sponges; separate paths so overlaps don't XOR-cancel. */
function tubes(rng: Rng): OrganismPath[] {
  const n = int(rng, 3, 5);
  const spread = 34 / n;
  const paths: OrganismPath[] = [];
  for (let i = 0; i < n; i++) {
    const x = 32 + (i - (n - 1) / 2) * spread + lerp(rng, -2, 2);
    paths.push({
      d: tube(x, 54, lerp(rng, 16, 30), lerp(rng, 9, 12), lerp(rng, -3.5, 3.5)),
      evenodd: true,
    });
  }
  return paths;
}

/** Blades all swept by the same current, taller blades swaying further. */
function seagrass(rng: Rng): OrganismPath[] {
  const n = int(rng, 4, 6);
  const sway = lerp(rng, 4, 9) * (rng() < 0.5 ? -1 : 1);
  const spread = 36 / n;
  let d = "";
  for (let i = 0; i < n; i++) {
    const x = 32 + (i - (n - 1) / 2) * spread + lerp(rng, -1.5, 1.5);
    const h = lerp(rng, 16, 30);
    d += blade(x, 54, h, sway * (h / 30), lerp(rng, 2.2, 3.4));
  }
  return [{ d }];
}

/** Mound of tangent bubbles; single nonzero path unions overlaps cleanly. */
function bubbles(rng: Rng): OrganismPath[] {
  const rows: Array<[number, number]> = [[3, 6.5], [int(rng, 2, 3), 5.5], [int(rng, 1, 2), 4.5]];
  let d = "";
  let y = 54;
  for (const [count, baseR] of rows) {
    const rad = baseR + lerp(rng, -0.8, 0.8);
    y -= rad;
    for (let i = 0; i < count; i++) {
      d += circle(32 + (i - (count - 1) / 2) * rad * 1.7 + lerp(rng, -1, 1), y + lerp(rng, -1, 1), rad);
    }
    y -= rad * 0.6;
  }
  return [{ d }];
}

/** Forking antler stems, thick round-capped strokes. */
function staghorn(rng: Rng): OrganismPath[] {
  const stems = int(rng, 2, 3);
  const spread = stems === 2 ? 16 : 13;
  const mid = Math.floor(stems / 2);
  let d = "";
  for (let s = 0; s < stems; s++) {
    const x = 32 + (s - (stems - 1) / 2) * spread + lerp(rng, -1.5, 1.5);
    const h = s === mid ? lerp(rng, 24, 32) : lerp(rng, 14, 20);
    d += `M${r2(x)} 54V${r2(54 - h)}`;
    const forks = int(rng, 1, 2);
    for (let f = 0; f < forks; f++) {
      const fy = 54 - h * lerp(rng, 0.35, 0.75);
      const dir = f % 2 === 0 ? 1 : -1;
      const fl = lerp(rng, 6, 10);
      d +=
        `M${r2(x)} ${r2(fy)}` +
        `C${r2(x)} ${r2(fy - fl * 0.5)} ${r2(x + dir * fl * 0.7)} ${r2(fy - fl * 0.5)} ${r2(x + dir * fl * 0.8)} ${r2(fy - fl)}`;
    }
  }
  return [{ d, stroke: 5 }];
}
```

- [ ] **Step 4: Run tests** — PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat: grounded organism families — tubes, seagrass, bubbles, staghorn"`

---

### Task 4: Grounded families B — brain, scallop, shell, nudibranch

**Files:** same two files.

- [ ] **Step 1: Extend the test's family list to all 11** (`FAMILIES` spread works: `for (const fam of FAMILIES)`) with the anchor lookup covering the remaining four (all `"grounded"`). Also add a distribution test:

```ts
test("every family is reachable and personal-free aliases cover the space", () => {
  const seen = new Set(FAMILIES.map((f) => generateFamily(f, 7).family));
  expect(seen.size).toEqual(11);
});
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement**

```ts
/** Dome with capsule groove cutouts — grooves shrink near the rim so they stay inside. */
function brain(rng: Rng): OrganismPath[] {
  const rw = lerp(rng, 17, 20);
  const rh = lerp(rng, 13, 16);
  let d = `M${r2(32 - rw)} 54A${r2(rw)} ${r2(rh)} 0 0 1 ${r2(32 + rw)} 54Z`;
  const grooves = int(rng, 4, 6);
  for (let i = 0; i < grooves; i++) {
    const th = lerp(rng, 0.3, Math.PI - 0.3);
    const rad = lerp(rng, 0.2, 0.55);
    const x = 32 - Math.cos(th) * rw * rad;
    const y = 54 - Math.sin(th) * rh * rad - 2.5;
    d += slot(x, y, lerp(rng, 5, 9) * (1 - rad * 0.5), 1.6, lerp(rng, -0.9, 0.9));
  }
  return [{ d, evenodd: true }];
}

/** Ray fan on a stem foot. */
function scallop(rng: Rng): OrganismPath[] {
  const n = int(rng, 7, 9);
  const len = lerp(rng, 13, 16);
  const spreadRad = (lerp(rng, 150, 190) * Math.PI) / 180;
  const baseY = 44;
  let rays = "";
  for (let i = 0; i < n; i++) {
    const th = -Math.PI / 2 + (i / (n - 1) - 0.5) * spreadRad;
    rays += `M32 ${baseY}L${r2(32 + Math.cos(th) * len)} ${r2(baseY + Math.sin(th) * len)}`;
  }
  const foot =
    `M27 54C27 49 29 46 29.5 ${r2(baseY)}L34.5 ${r2(baseY)}C35 46 37 49 37 54Z`;
  return [{ d: rays, stroke: lerp(rng, 3.4, 4.2) }, { d: foot }];
}

/** Scallop-rimmed clam fan with radial rib slots. */
function shell(rng: Rng): OrganismPath[] {
  const k = int(rng, 5, 7);
  const rad = lerp(rng, 15, 18);
  const spreadRad = lerp(rng, 1.9, 2.3);
  const start = -Math.PI / 2 - spreadRad / 2;
  let d = `M32 52L${r2(32 + Math.cos(start) * rad)} ${r2(52 + Math.sin(start) * rad)}`;
  for (let i = 1; i <= k; i++) {
    const th = start + (i / k) * spreadRad;
    const bump = r2((rad * spreadRad) / k / 1.6);
    d += `A${bump} ${bump} 0 0 1 ${r2(32 + Math.cos(th) * rad)} ${r2(52 + Math.sin(th) * rad)}`;
  }
  d += "Z";
  const ribs = int(rng, 3, 5);
  for (let i = 1; i <= ribs; i++) {
    const th = start + (i / (ribs + 1)) * spreadRad;
    d += slot(32 + Math.cos(th) * rad * 0.55, 52 + Math.sin(th) * rad * 0.55, rad * 0.55, 1.1, th);
  }
  return [{ d, evenodd: true }];
}

/** Slug with humped back, two rhinophores, and spot cutouts; faces left or right. */
function nudibranch(rng: Rng): OrganismPath[] {
  const flip = rng() < 0.5 ? -1 : 1;
  const len = lerp(rng, 14, 17);
  const h = lerp(rng, 10, 13);
  const tailY = 54 - lerp(rng, 4, 7);
  const hx = 32 + flip * len, tx = 32 - flip * len;
  const body =
    `M${r2(tx)} 54` +
    `C${r2(tx)} ${r2(tailY)} ${r2(32 - flip * len * 0.5)} ${r2(54 - h)} 32 ${r2(54 - h)}` +
    `C${r2(32 + flip * len * 0.55)} ${r2(54 - h)} ${r2(hx)} ${r2(54 - h * 0.55)} ${r2(hx)} ${r2(54 - h * 0.3)}` +
    `C${r2(hx)} ${r2(54 - h * 0.05)} ${r2(32 + flip * len * 0.6)} 54 32 54Z`;
  let dots = "";
  const nDots = int(rng, 2, 4);
  for (let i = 0; i < nDots; i++) {
    dots += circle(
      32 - flip * len * 0.5 + flip * (i / nDots) * len * 0.9,
      54 - h * lerp(rng, 0.35, 0.6),
      lerp(rng, 1.1, 1.7),
    );
  }
  const hb = 32 + flip * len * 0.72;
  let horns = "";
  for (const off of [0, flip * 4]) {
    const bx = hb - off, by = 54 - h * 0.78;
    horns += `M${r2(bx)} ${r2(by)}C${r2(bx + flip)} ${r2(by - 3)} ${r2(bx + flip * 2)} ${r2(by - 4)} ${r2(bx + flip * 2.5)} ${r2(by - 6)}`;
  }
  return [{ d: body + dots, evenodd: true }, { d: horns, stroke: 2.2 }];
}
```

Register all four in `GENERATORS`.

- [ ] **Step 4: Run tests** — PASS (all 11 families, 50 seeds each).
- [ ] **Step 5: Commit** — `git commit -am "feat: grounded organism families — brain, scallop, shell, nudibranch"`

---

### Task 5: Chunky fan coral + `organismFor` + `SpaceGlyph` rewrite

**Files:**
- Modify: `frontend/src/components/organisms.ts`
- Rewrite: `frontend/src/components/spaceGlyph.tsx`
- Rewrite test: `frontend/src/spaceGlyph.test.ts`
- Modify: `frontend/src/views/Home.tsx` (glyph sizing only, if list rows need a size tweak for square aspect)

**Interfaces (produced):**

```ts
// organisms.ts
export const CORAL_PATHS: readonly OrganismPath[]; // chunky fan coral, grounded
export function organismFor(alias: string): Organism; // personal → coral, else hash
// spaceGlyph.tsx — component contract unchanged:
export function SpaceGlyph({ alias, color, size }: { alias: string; color: string; size?: number }): JSX.Element; // renders size × size
```

- [ ] **Step 1: Rewrite `spaceGlyph.test.ts` as failing tests**

```ts
import { describe, expect, test } from "bun:test";
import { FAMILIES, organismFor } from "./components/organisms";

describe("organismFor", () => {
  test("personal is always the fan coral", () => {
    expect(organismFor("personal").family).toEqual("coral");
  });
  test("deterministic per alias", () => {
    expect(organismFor("roadtrip")).toEqual(organismFor("roadtrip"));
  });
  test("non-personal aliases stay inside the hashed families", () => {
    for (const alias of ["roadtrip", "household", "boekenclub", "diepzee", "atelier"]) {
      expect(FAMILIES).toContain(organismFor(alias).family as never);
    }
  });
  test("a realistic corpus reaches many families", () => {
    const corpus = ["roadtrip","household","boekenclub","diepzee","atelier","garden",
      "budget","recipes","wedding","band","chess","surf","lab","crew","tribe","nest"];
    const seen = new Set(corpus.map((a) => organismFor(a).family));
    expect(seen.size).toBeGreaterThanOrEqual(7);
  });
});
```

- [ ] **Step 2: Run** — `bun test src/spaceGlyph.test.ts` → FAIL.

- [ ] **Step 3: Implement.** In `organisms.ts`:

```ts
/**
 * The brand fan coral as a chunky silhouette — trunk forking into a dome
 * of thick round-capped branches. Fixed, never hashed: pinned to the
 * personal space and reused by ReefMark/FrondGlyph as the brand mark.
 */
export const CORAL_PATHS: readonly OrganismPath[] = [
  {
    d:
      "M32 54V40" +
      "M32 46C32 40 24 40 22 30C21 26 20 24 18 22" +
      "M22 30C23 26 26 25 27 20" +
      "M32 44C32 37 39 38 42 28C43 25 45 23 47 22" +
      "M42 28C41 24 38 24 37 19" +
      "M32 42C31 34 32 30 32 24C32 21 31 18 29 15" +
      "M32 24C33 21 35 20 36 16",
    stroke: 5.2,
  },
];

/** Deterministic organism for a space's alias — the reef's genome function. */
export function organismFor(alias: string): Organism {
  if (alias === "personal") return { family: "coral", anchor: "grounded", paths: [...CORAL_PATHS] };
  const seed = fnv1a(alias);
  return generateFamily(FAMILIES[seed % FAMILIES.length]!, seed);
}
```

Rewrite `spaceGlyph.tsx` to a thin renderer (delete the six old path components and `ORGANISMS`):

```tsx
import { organismFor, type OrganismPath } from "./organisms";

/** Renders one OrganismPath — filled silhouette or thick round-capped stroke. */
function OrgPath({ p }: { p: OrganismPath }) {
  return p.stroke !== undefined ? (
    <path d={p.d} fill="none" stroke="currentColor" strokeWidth={p.stroke}
      strokeLinecap="round" strokeLinejoin="round" />
  ) : (
    <path d={p.d} fill="currentColor" fillRule={p.evenodd ? "evenodd" : "nonzero"} />
  );
}

interface SpaceGlyphProps {
  alias: string;
  color: string;
  /** Rendered width AND height in px — the organism box is square. Default 20. */
  size?: number;
}

/** A space's own procedurally grown reef organism. */
export function SpaceGlyph({ alias, color, size = 20 }: SpaceGlyphProps) {
  const organism = organismFor(alias);
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} style={{ color }} aria-hidden="true">
      {organism.paths.map((p, i) => (
        <OrgPath key={i} p={p} />
      ))}
    </svg>
  );
}
```

Check `Home.tsx` call sites still look right (they pass `size` only; square is fine — bump sizes if rows look sparse: list row ~22, card ~28).

- [ ] **Step 4: Run all frontend tests** — `bun test` → PASS (fix any imports of removed `ORGANISMS`).
- [ ] **Step 5: Commit** — `git commit -am "feat: procedural organismFor + SpaceGlyph over the parametric families"`

---

### Task 6: Dev gallery route

**Files:**
- Create: `frontend/src/views/Gallery.tsx`
- Modify: `frontend/src/App.tsx` (add dev-only route)

**Interfaces:** consumes `FAMILIES`, `generateFamily`, `organismFor`, `SpaceGlyph`, `spaceColor`.

- [ ] **Step 1: Implement** (no unit test — this is a dev-only tuning surface, excluded from prod builds by the DEV gate):

```tsx
/**
 * Dev-only organism gallery: every family × a seed sweep × the hue
 * palette, at chip (20px) and tile (64px) sizes — the tuning surface for
 * parameter ranges. Never mounted in production builds.
 */
import { FAMILIES, generateFamily, type OrganismPath } from "../components/organisms";
import { spaceColor } from "../components/spaceColor";

const SEEDS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89];
const HUES = ["h1","h2","h3","h4","h5","h6","h7"].map((a) => spaceColor(a).base);

function Paths({ paths }: { paths: readonly OrganismPath[] }) {
  return (
    <>
      {paths.map((p, i) =>
        p.stroke !== undefined ? (
          <path key={i} d={p.d} fill="none" stroke="currentColor" strokeWidth={p.stroke}
            strokeLinecap="round" strokeLinejoin="round" />
        ) : (
          <path key={i} d={p.d} fill="currentColor" fillRule={p.evenodd ? "evenodd" : "nonzero"} />
        ),
      )}
    </>
  );
}

export function Gallery() {
  return (
    <div style={{ padding: 24, display: "grid", gap: 24 }}>
      {FAMILIES.map((fam) => (
        <section key={fam}>
          <h2 style={{ marginBottom: 8 }}>{fam}</h2>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
            {SEEDS.map((seed, i) => {
              const org = generateFamily(fam, seed);
              return (
                <div key={seed} style={{ color: HUES[i % HUES.length], display: "flex",
                  flexDirection: "column", alignItems: "center", gap: 4 }}>
                  <svg viewBox="0 0 64 64" width={64} height={64}><Paths paths={org.paths} /></svg>
                  <svg viewBox="0 0 64 64" width={20} height={20}><Paths paths={org.paths} /></svg>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
```

In `App.tsx` inside `<Routes>`:

```tsx
{import.meta.env.DEV && <Route path="/gallery" element={<Gallery />} />}
```

(If `import.meta.env` isn't available under the bun dev server, use `process.env.NODE_ENV !== "production"` — check `dev.ts` to see what's defined.)

- [ ] **Step 2: Visual pass.** Run the dev server (`cd frontend && bun run dev`), open `/app/gallery` with Playwright (`mcp__plugin_playwright_playwright__browser_navigate` + `browser_take_screenshot`). Review every family at both sizes: equal visual mass, silhouettes read at 20px, nothing pokes outside the 64-box, no evenodd accidents. Tune parameter ranges in `organisms.ts` and re-screenshot until the sheet reads like the reference. **This step is iterative and where most of the task's time goes.**
- [ ] **Step 3: Run tests** — `bun test` → PASS (determinism tests pin any tuned ranges).
- [ ] **Step 4: Commit** — `git commit -am "feat: dev-only organism gallery for parameter tuning"`

---### Task 7: Brand mark — chunky coral in ReefMark/FrondGlyph, reef.svg, icons

**Files:**
- Modify: `frontend/src/components/ReefMark.tsx` (replace traced path with `CORAL_PATHS`)
- Modify: `frontend/public/reef.svg` (same geometry, hand-authored)
- Regenerate: `frontend/public/reef-icon.png` via `bun run scripts/render-icons.ts`
- Check: `frontend/src/components/AppShell.tsx`, `Sidebar.tsx`, `views/SignedOut.tsx` (FrondGlyph callers — sizing)

**Interfaces:** `CoralPaths` stays exported from `ReefMark.tsx` but becomes a renderer of `CORAL_PATHS` taking paint from context; `GLYPH_ASPECT` becomes `1` (square) — update the three `FrondGlyph` callers' `size` props if the square aspect changes their layout.

- [ ] **Step 1: Rewrite `ReefMark.tsx`.** Delete `CORAL_D`, the transforms, and the old crop. New:

```tsx
import { useId } from "react";
import { CORAL_PATHS } from "./organisms";

/** The coral geometry; paint via `paint` (a color or url(#gradient) ref). */
export function CoralPaths({ paint }: { paint: string }) {
  return (
    <>
      {CORAL_PATHS.map((p, i) => (
        <path key={i} d={p.d} fill="none" stroke={paint}
          strokeWidth={p.stroke} strokeLinecap="round" strokeLinejoin="round" />
      ))}
    </>
  );
}

export function ReefMark({ size = 30, className }: ReefMarkProps) {
  const gradientId = useId();
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} role="img" aria-label="reef" className={className}>
      <defs>
        <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1="0" y1="54" x2="0" y2="15">
          <stop offset="0" stopColor="#0d9488" />
          <stop offset="1" stopColor="#5eead4" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="var(--mark-tile)" />
      <CoralPaths paint={`url(#${gradientId})`} />
    </svg>
  );
}

/** Square crop tight to the coral (x 13..52, y 12..54 → pad to square). */
const GLYPH_VIEWBOX = "11 12 42 42";
export const GLYPH_ASPECT = 1;

export function FrondGlyph({ color, size = 20 }: FrondGlyphProps) {
  return (
    <svg viewBox={GLYPH_VIEWBOX} width={size} height={size}
      preserveAspectRatio="xMidYMax meet" aria-hidden="true">
      <CoralPaths paint={color} />
    </svg>
  );
}
```

- [ ] **Step 2: Rewrite `public/reef.svg`** with the same geometry inline (literal gradient id, light tile — it stays the favicon):

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="c" gradientUnits="userSpaceOnUse" x1="0" y1="54" x2="0" y2="15">
      <stop offset="0" stop-color="#0d9488"/><stop offset="1" stop-color="#5eead4"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="14" fill="#f0fdfa"/>
  <path d="M32 54V40M32 46C32 40 24 40 22 30C21 26 20 24 18 22M22 30C23 26 26 25 27 20M32 44C32 37 39 38 42 28C43 25 45 23 47 22M42 28C41 24 38 24 37 19M32 42C31 34 32 30 32 24C32 21 31 18 29 15M32 24C33 21 35 20 36 16"
    fill="none" stroke="url(#c)" stroke-width="5.2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
```

(The `d` must be copy-pasted from `CORAL_PATHS` after gallery tuning — keep them in sync; note it in both file headers.)

- [ ] **Step 3: Regenerate icons** — `cd frontend && bun run scripts/render-icons.ts`.
- [ ] **Step 4: Verify** — `bun test` from `frontend/`, then backend statics: `uv run pytest tests/test_web_static.py -q` from the repo root → PASS.
- [ ] **Step 5: Visual check** — Playwright screenshot of the app shell (sidebar lockup, splash) in light + dark.
- [ ] **Step 6: Commit** — `git commit -am "feat: chunky silhouette fan coral as the brand mark; regenerate icons"`

---

### Task 8: Reef/cove copy sweep

**Files:**
- Modify: `frontend/src/views/Home.tsx` (`<h1>Spaces</h1>` → `<h1>Reef</h1>`)
- Modify: `frontend/src/components/Sidebar.tsx` (side-label `Spaces` → `Reef`)
- Modify: every user-visible string containing "space"/"Space" — sweep with `grep -rn '"[^"]*[Ss]pace' frontend/src --include='*.tsx'` and change copy (not identifiers) to "cove": e.g. "New space" → "New cove", `aria-label="Create a space"` → `"Create a cove"`, invite/leave/manage strings, `pageMeta` titles.
- Test: update `frontend/src/spacesView.test.ts` / `pageMeta.test.ts` if they assert copy.

- [ ] **Step 1: Sweep and edit.** Rules: standalone collection label → "Reef"; a single space in a sentence → "cove"; alias interpolations (`People in {alias}`) untouched; routes/identifiers/API paths untouched; code comments describing the backend concept untouched.
- [ ] **Step 2: Run tests** — `bun test` → fix copy assertions → PASS.
- [ ] **Step 3: Visual check** — Playwright pass over Home, sidebar, members sheet, new-space flow: no leftover "Spaces" labels on screen.
- [ ] **Step 4: Commit** — `git commit -am "feat: UI copy — the reef and its coves, Spaces label retired"`

---

### Task 9: Rebase and full verification

- [ ] **Step 1:** `git fetch origin main` then `git rebase origin/main` (memory note: always fetch before rebasing here; lowercase enum values if raw SQL conflicts appear).
- [ ] **Step 2:** Resolve conflicts, rerun everything: `cd frontend && bun test`, `uv run pytest -q` at root.
- [ ] **Step 3:** Playwright smoke of the app (home, a space, a page) — glyphs, mark, copy all correct post-rebase.

---

### Task 10: Marketing page restyle (`docs/how-it-works.html`)

**Files:**
- Modify: `docs/how-it-works.html`

- [ ] **Step 1: Inventory.** Read the page fully. Note fonts (Seravek stack), palette tokens, header/lockup, section structure, any "space(s)" copy.
- [ ] **Step 2: Restyle.**
  - Font: self-host or system-fallback Nunito to match the app (`frontend/src/app.css` shows the app's `@font-face` setup — mirror the stack; if the page must stay a single self-contained file, use `font-family: Nunito, -apple-system, "Segoe UI", sans-serif` and accept system fallback when the font isn't installed, or inline the woff2 as a data URI if size is acceptable).
  - Palette: adopt the app's current token values (`frontend/src/tokens.css`) for bg/surface/ink/accent in both light and dark blocks.
  - Header: inline the new reef.svg mark (copy the `<svg>` from Task 7 Step 2) into the lockup.
  - Organism accents: 2–3 inline organism SVGs as section markers (generate with a small scratch script: `bun -e` calling `generateFamily`, paste path output) — sparingly, per spec.
  - Copy sweep: reef/cove language.
- [ ] **Step 3: Critical Playwright review loop.** Serve the file (`python3 -m http.server` in `docs/` or open `file://`), screenshot at 390px and 1280px, light and dark. Review strictly: type scale hierarchy, spacing rhythm, contrast (muted text on bg ≥ 4.5:1), alignment, dark-mode gradient legibility. Fix, re-screenshot, repeat until it would pass a design review — at least two full iterations.
- [ ] **Step 4: Commit** — `git commit -am "feat: marketing page on the reef visual language"`

---

## Self-review notes

- Spec §1 → Tasks 1–5; §2 → Task 7; §3 → Task 8; §4 → Task 6; §5 → tests in Tasks 1–5 + statics in Task 7; §6 → Tasks 9–10. No gaps.
- `generateFamily` name/signature consistent across Tasks 2, 4, 5, 6, 10.
- `CORAL_PATHS` produced in Task 5, consumed in Task 7 and 10.
- Gallery (Task 6) intentionally precedes brand mark (Task 7) so coral tuning happens with the gallery available — the coral can be added to the gallery grid ad hoc during Step 2 if useful.
