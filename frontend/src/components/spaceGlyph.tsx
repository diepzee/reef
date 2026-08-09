/**
 * Per-space organism glyphs — every space grows its own kind of reef
 * life, the way `spaceColor` gives it its own hue. The personal space is
 * always the brand's fan coral (the one space every principal shares,
 * same pinning discipline as its seafoam hue); every other alias hashes
 * across a six-organism family by summing character codes.
 *
 * The modulus (6) deliberately differs from the hue palette's 7, so hue
 * and organism assignments drift apart: 42 distinct combinations before
 * two spaces can fully collide.
 *
 * All organisms share one visual weight (≈5-unit strokes / equivalent
 * fill mass) and the 64-box ground line at y=50, cropped so the glyph's
 * bottom edge is the organism's base — same baseline discipline as
 * `FrondGlyph` in `ReefMark.tsx`.
 */

import type { JSX } from "react";

import { CoralPaths } from "./ReefMark";

/** The organisms a non-personal space can hash to. */
export const ORGANISMS = [
  "anemone",
  "staghorn",
  "kelp",
  "polyps",
  "seagrass",
  "brain",
] as const;

/** One kind of reef life: a hashed family member, or the brand coral (personal only). */
export type Organism = (typeof ORGANISMS)[number] | "coral";

/**
 * Deterministic organism for a space's alias.
 *
 * :param alias: the space's alias, e.g. "personal" or "roadtrip"
 * :returns: the organism kind this space always renders as
 */
export function organismFor(alias: string): Organism {
  if (alias === "personal") return "coral";
  let sum = 0;
  for (const char of alias) {
    sum += char.charCodeAt(0);
  }
  return ORGANISMS[sum % ORGANISMS.length]!;
}

/** Tentacled anemone on a mound foot. */
function AnemonePaths() {
  return (
    <>
      <path d="M19 50c0-3.5 5.5-5.5 13-5.5s13 2 13 5.5z" fill="currentColor" />
      <g stroke="currentColor" strokeWidth={5.2} strokeLinecap="round" fill="none">
        <path d="M22 45c-2-5-6-8-7-13" />
        <path d="M27 44c-1-6-2-10-1-16" />
        <path d="M32 44c0-7 0-11 0-17" />
        <path d="M37 44c1-6 2-10 1-16" />
        <path d="M42 45c2-5 6-8 7-13" />
      </g>
    </>
  );
}

/** Three forked staghorn stems. */
function StaghornPaths() {
  return (
    <g stroke="currentColor" strokeWidth={5} strokeLinecap="round" fill="none">
      <path d="M32 50V26" />
      <path d="M32 34C32 29 38 29 39 22" />
      <path d="M32 42C32 37 26 37 25 30" />
      <path d="M19 50V38" />
      <path d="M19 44C19 40 14 40 13 34" />
      <path d="M45 50V38" />
      <path d="M45 44C45 40 50 40 51 34" />
    </g>
  );
}

/** Five kelp fronds swept the same way by the current. */
function KelpPaths() {
  return (
    <g stroke="currentColor" strokeWidth={5} strokeLinecap="round" fill="none">
      <path d="M17 50c1-5 4-8 4-13" />
      <path d="M25 50c1-7 4-11 4-19" />
      <path d="M33 50c1-9 4-14 4-24" />
      <path d="M41 50c1-7 4-11 4-18" />
      <path d="M48 50c1-4 3-7 3-11" />
    </g>
  );
}

/** Thin polyp stems with dot tips. */
function PolypsPaths() {
  return (
    <>
      <g stroke="currentColor" strokeWidth={3.6} strokeLinecap="round" fill="none">
        <path d="M18 50V33" />
        <path d="M25 50V25" />
        <path d="M32 50V29" />
        <path d="M39 50V23" />
        <path d="M46 50V35" />
      </g>
      <g fill="currentColor">
        <circle cx={18} cy={29} r={3.3} />
        <circle cx={25} cy={21} r={3.3} />
        <circle cx={32} cy={25} r={3.3} />
        <circle cx={39} cy={19} r={3.3} />
        <circle cx={46} cy={31} r={3.3} />
      </g>
    </>
  );
}

/** Five filled tapered seagrass blades. */
function SeagrassPaths() {
  return (
    <g fill="currentColor">
      <path d="M15 50c-1-8 1-14 4-20c1 7 0 14-1 20z" />
      <path d="M23 50c-1-11 1-19 5-27c2 9 0 19-2 27z" />
      <path d="M31 50c-1-13 2-22 6-32c2 11-1 22-3 32z" />
      <path d="M40 50c-1-10 1-17 5-24c2 8 0 16-2 24z" />
      <path d="M48 50c-1-7 1-12 4-17c1 6 0 11-1 17z" />
    </g>
  );
}

/** Three grooved brain-coral bands piling into a dome. */
function BrainPaths() {
  return (
    <g fill="currentColor">
      <path d="M15 50c-0.5-6 6-9 17-9c11 0 17 3 16.5 9z" />
      <path d="M20 40c0-5.5 5-8 13-8c8 0 12.5 2.5 12 8z" />
      <path d="M25 32c0-4.5 3.5-6.5 8-6.5c4.5 0 7.5 2 7 6.5z" />
    </g>
  );
}

const PATHS: Record<Organism, () => JSX.Element> = {
  coral: CoralPaths,
  anemone: AnemonePaths,
  staghorn: StaghornPaths,
  kelp: KelpPaths,
  polyps: PolypsPaths,
  seagrass: SeagrassPaths,
  brain: BrainPaths,
};

/** Crop whose bottom edge is the organisms' shared ground line (y=50). */
const GLYPH_VIEWBOX = "11 15 42 37";
/** Width/height ratio of {@link GLYPH_VIEWBOX}. */
const GLYPH_ASPECT = 42 / 37;

interface SpaceGlyphProps {
  /** The space's alias — decides which organism renders. */
  alias: string;
  /** Glyph color — typically the space's hue (`spaceColor(alias)`). */
  color: string;
  /** Rendered HEIGHT in px (width = height × the crop's aspect). Default 20. */
  size?: number;
}

/** A space's own reef organism, single-colored, standing on its baseline. */
export function SpaceGlyph({ alias, color, size = 20 }: SpaceGlyphProps) {
  const Paths = PATHS[organismFor(alias)];
  return (
    <svg
      viewBox={GLYPH_VIEWBOX}
      width={size * GLYPH_ASPECT}
      height={size}
      preserveAspectRatio="xMidYMax meet"
      style={{ color }}
      aria-hidden="true"
    >
      {/* The wrapper fill paints CoralPaths (attribute-less); the family's
          own stroke/fill attributes override it where they need to. */}
      <g fill="currentColor">
        <Paths />
      </g>
    </svg>
  );
}
