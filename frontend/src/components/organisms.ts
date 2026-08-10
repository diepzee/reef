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
  const rows: Array<[number, number]> = [
    [3, 6.5],
    [int(rng, 2, 3), 5.5],
    [int(rng, 1, 2), 4.5],
  ];
  let d = "";
  let y = 54;
  for (const [count, baseR] of rows) {
    const rad = baseR + lerp(rng, -0.8, 0.8);
    y -= rad;
    for (let i = 0; i < count; i++) {
      d += circle(
        32 + (i - (count - 1) / 2) * rad * 1.7 + lerp(rng, -1, 1),
        y + lerp(rng, -1, 1),
        rad,
      );
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
  const foot = `M27 54C27 49 29 46 29.5 ${r2(baseY)}L34.5 ${r2(baseY)}C35 46 37 49 37 54Z`;
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
  const hx = 32 + flip * len;
  const tx = 32 - flip * len;
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
    const bx = hb - off;
    const by = 54 - h * 0.78;
    horns += `M${r2(bx)} ${r2(by)}C${r2(bx + flip)} ${r2(by - 3)} ${r2(bx + flip * 2)} ${r2(by - 4)} ${r2(bx + flip * 2.5)} ${r2(by - 6)}`;
  }
  return [{ d: body + dots, evenodd: true }, { d: horns, stroke: 2.2 }];
}

const GENERATORS: Partial<Record<Family, Generator>> = {
  sunAnemone,
  flower,
  spiral,
  tubes,
  seagrass,
  bubbles,
  staghorn,
  brain,
  scallop,
  shell,
  nudibranch,
};

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
