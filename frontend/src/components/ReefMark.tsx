/**
 * The rif mark as inline SVG, mirroring `public/reef.svg`'s frond colony —
 * but with the tile fill pointed at the `--mark-tile` token instead of a
 * baked-in light seafoam, so the same asset reads correctly on both a
 * light and a bioluminescent-dark ground (see tokens.css's `--mark-tile`
 * and the identity-pass spec). `reef.svg` itself is untouched and stays
 * the favicon, which always wants the light tile regardless of theme.
 */

import { useId } from "react";

/**
 * The seabed bar that fuses the fronds' rounded bases into one closed
 * base instead of separate "toes". Kept OUT of any gradient-stroked
 * group: the bar is a zero-height horizontal line, and objectBoundingBox
 * gradients degenerate on a zero-height bbox (Chrome paints nothing), so
 * it must be stroked with the gradient's literal base color instead.
 */
const SEABED_PATH = "M20 50h24";

/**
 * The seven frond paths shared by {@link ReefMark} and {@link FrondGlyph},
 * copied from `public/reef.svg`.
 */
const FROND_PATHS = [
  "M20 50C20 45.2 13 42.2 13 38",
  "M24 50C24 42.4 19 37.7 19 31",
  "M28 50C28 39.6 25 33.1 25 24",
  "M32 50c-2.5-11 2.5-21 0-32",
  "M36 50C36 39.6 39 33.1 39 24",
  "M40 50C40 42.4 45 37.7 45 31",
  "M44 50C44 45.2 51 42.2 51 38",
] as const;

interface ReefMarkProps {
  /** Rendered width/height in px — the viewBox is square. Default 30 (the identity pass's "larger mark"). */
  size?: number;
  /** Extra class applied to the root `<svg>`, for layout hooks like `.side-brand-icon`. */
  className?: string;
}

/**
 * The full rif mark: a rounded tile (filled `var(--mark-tile)`) behind the
 * frond colony's teal gradient. Used in the sidebar brand row and the
 * mobile header in place of the old static `<img src={reefIcon}>`.
 */
export function ReefMark({ size = 30, className }: ReefMarkProps) {
  const gradientId = useId();
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      role="img"
      aria-label="rif"
      className={className}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="#0d9488" />
          <stop offset="1" stopColor="#5eead4" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="var(--mark-tile)" />
      <path
        d={SEABED_PATH}
        stroke="#0d9488"
        strokeWidth={4}
        strokeLinecap="round"
        fill="none"
      />
      <g stroke={`url(#${gradientId})`} strokeWidth={4} strokeLinecap="round" fill="none">
        {FROND_PATHS.map((d) => (
          <path key={d} d={d} />
        ))}
      </g>
    </svg>
  );
}

interface FrondGlyphProps {
  /** Stroke color for the frond paths — typically a space's hue pair's `light` value. */
  color: string;
  /** Rendered width/height in px. Default 20 (fits inside a 36px chip). */
  size?: number;
}

/**
 * The mark's frond colony alone, no tile — a single-color glyph for the
 * home card's tinted space chip, stroked with the space's hue.
 */
export function FrondGlyph({ color, size = 20 }: FrondGlyphProps) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <g stroke={color} strokeWidth={4} strokeLinecap="round" fill="none">
        <path d={SEABED_PATH} />
        {FROND_PATHS.map((d) => (
          <path key={d} d={d} />
        ))}
      </g>
    </svg>
  );
}
