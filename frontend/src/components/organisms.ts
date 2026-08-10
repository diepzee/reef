/**
 * Procedural reef organisms — every space grows a unique individual.
 *
 * The pipeline is pure and deterministic: alias → `fnv1a` seed →
 * `mulberry32` PRNG → per-family parameters → SVG path data. Each family
 * (spec: docs/superpowers/specs/2026-08-10-procedural-reef-organisms-design.md)
 * is a small generator drawing its parameters *in a fixed order* from the
 * PRNG stream — reordering draws is a breaking change to every existing
 * space's organism.
 *
 * Geometry lives in a 64-box: grounded families stand on the y=54
 * baseline, radial families center on (32, 32). Filled silhouettes use
 * `currentColor`; holes (mouths, grooves, spots) are evenodd subpaths, so
 * shapes that must *overlap* (tube sponges) ship as separate paths — two
 * subpaths of one evenodd path would XOR-cancel where they cross.
 */

export type Anchor = "grounded" | "radial";

/** One renderable piece: a fill silhouette, or a thick round-capped stroke. */
export interface OrganismPath {
  d: string;
  /** When set, render as a stroke of this width instead of a fill. */
  stroke?: number;
  /** When true, fill with the evenodd rule (subpath holes). */
  evenodd?: boolean;
}

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
  const dx = Math.cos(theta);
  const dy = Math.sin(theta);
  const px = -dy;
  const py = dx;
  const bw = w * 0.4;
  const mx = x + dx * len * 0.62;
  const my = y + dy * len * 0.62;
  const tx = x + dx * len;
  const ty = y + dy * len;
  return (
    `M${r2(x + px * bw)} ${r2(y + py * bw)}` +
    `C${r2(mx + px * w)} ${r2(my + py * w)} ${r2(tx + px * w * 0.55)} ${r2(ty + py * w * 0.55)} ${r2(tx)} ${r2(ty)}` +
    `C${r2(tx - px * w * 0.55)} ${r2(ty - py * w * 0.55)} ${r2(mx - px * w)} ${r2(my - py * w)} ${r2(x - px * bw)} ${r2(y - py * bw)}Z`
  );
}

/** Tapered seagrass blade rooted at (x, y0), tip swayed sideways. */
function blade(x: number, y0: number, h: number, sway: number, w: number): string {
  const tx = x + sway;
  const ty = y0 - h;
  return (
    `M${r2(x - w)} ${r2(y0)}` +
    `C${r2(x - w * 0.9)} ${r2(y0 - h * 0.45)} ${r2(tx - w * 0.35)} ${r2(ty + h * 0.25)} ${r2(tx)} ${r2(ty)}` +
    `C${r2(tx + w * 0.15)} ${r2(ty + h * 0.3)} ${r2(x + w * 0.95)} ${r2(y0 - h * 0.4)} ${r2(x + w)} ${r2(y0)}Z`
  );
}

/** Leaning tube sponge with an elliptical mouth hole (render with evenodd). */
function tube(x: number, y0: number, h: number, w: number, lean: number): string {
  const topX = x + lean;
  const topY = y0 - h;
  const rx = w / 2;
  const ry = rx * 0.45;
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
  const dx = Math.cos(ang);
  const dy = Math.sin(ang);
  const px = -dy * hw;
  const py = dx * hw;
  const ax = x - (dx * len) / 2;
  const ay = y - (dy * len) / 2;
  const bx = x + (dx * len) / 2;
  const by = y + (dy * len) / 2;
  return (
    `M${r2(ax + px)} ${r2(ay + py)}A${r2(hw)} ${r2(hw)} 0 0 1 ${r2(ax - px)} ${r2(ay - py)}` +
    `L${r2(bx - px)} ${r2(by - py)}A${r2(hw)} ${r2(hw)} 0 0 1 ${r2(bx + px)} ${r2(by + py)}Z`
  );
}

/** The body plans a non-personal space can hash to. */
export const FAMILIES = [
  "sunAnemone",
  "tubes",
  "staghorn",
  "brain",
  "flower",
  "scallop",
  "spiral",
  "bubbles",
  "seagrass",
  "shell",
  "nudibranch",
] as const;

/** One kind of reef life: a hashed family, or the brand coral (personal only). */
export type Family = (typeof FAMILIES)[number] | "coral";

/** A grown individual: its family, how it anchors, and its renderable paths. */
export interface Organism {
  family: Family;
  anchor: Anchor;
  paths: OrganismPath[];
}

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
    const thMid = th + curl * 0.45;
    const thEnd = th + curl;
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

// Consumed by the remaining family generators (Tasks 3–4).
void blade;
void tube;
void slot;
