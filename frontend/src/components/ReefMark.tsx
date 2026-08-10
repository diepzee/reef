/**
 * The reef mark — the chunky silhouette fan coral from `organisms.ts`
 * (`CORAL_PATHS`), which is also the personal space's organism. Mirrors
 * `public/reef.svg` (still the favicon — that file carries the same path
 * data and must be kept in sync by hand). The tile fill points at
 * `--mark-tile` so the same asset reads correctly on light and
 * bioluminescent-dark grounds.
 *
 * The gradient is userSpaceOnUse on the 64-box (base y=54 → top y=15):
 * objectBoundingBox units degenerate on axis-aligned subpaths, and
 * user-space coords are safe for any geometry drawn in this box.
 */

import { useId } from "react";

import { CORAL_PATHS } from "./organisms";

/** The coral geometry; `paint` is a color or a `url(#gradient)` reference. */
export function CoralPaths({ paint }: { paint: string }) {
  return (
    <>
      {CORAL_PATHS.map((p, i) => (
        <path
          key={i}
          d={p.d}
          fill="none"
          stroke={paint}
          strokeWidth={p.stroke}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </>
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
          y1="54"
          x2="0"
          y2="15"
        >
          <stop offset="0" stopColor="#0d9488" />
          <stop offset="1" stopColor="#5eead4" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="var(--mark-tile)" />
      <CoralPaths paint={`url(#${gradientId})`} />
    </svg>
  );
}

/** Square crop tight to the coral (strokes span x 14..49, y 15..54, plus caps). */
const GLYPH_VIEWBOX = "11 11.5 42 45";
/** Width/height ratio of {@link GLYPH_VIEWBOX} — square since the redesign. */
export const GLYPH_ASPECT = 1;

interface FrondGlyphProps {
  /** Stroke color — a space hue, `var(--accent)`, or `currentColor`. */
  color: string;
  /** Rendered width AND height in px (the crop is square). Default 20. */
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
      width={size}
      height={size}
      preserveAspectRatio="xMidYMax meet"
      aria-hidden="true"
    >
      <CoralPaths paint={color} />
    </svg>
  );
}
