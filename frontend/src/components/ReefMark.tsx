/**
 * The reef mark as inline SVG, mirroring `public/reef.svg`'s fan coral —
 * a dome of thick forking branches vectorized from the approved reference
 * (spec: docs/superpowers/specs/2026-08-09-reef-visual-redesign-design.md).
 * The tile fill points at `--mark-tile` so the same asset reads correctly
 * on light and bioluminescent-dark grounds. `reef.svg` itself stays the
 * favicon, which always wants the light tile regardless of theme.
 *
 * The gradient is userSpaceOnUse on the 64-box (base y=50 -> top y=16):
 * objectBoundingBox units degenerate on axis-aligned subpaths, and
 * user-space coords are safe for any geometry drawn in this box.
 */

import { useId } from "react";

/** Potrace path of the fan coral, in the traced source's pixel space. */
const CORAL_D =
  "M1441 2805 c-40 -8 -78 -18 -84 -24 -7 -7 2 -69 29 -203 37 -181 39 -204 39 -368 0 -197 -16 -264 -76 -315 -39 -32 -54 -32 -89 3 -48 49 -55 94 -52 329 2 133 -1 234 -8 270 -18 92 -50 181 -67 187 -23 9 -173 -76 -173 -99 0 -2 14 -44 30 -93 67 -200 21 -303 -75 -169 -75 103 -95 127 -107 127 -17 0 -76 -70 -126 -149 l-46 -74 40 -33 c21 -19 84 -64 139 -101 129 -88 205 -165 249 -255 85 -170 11 -184 -169 -32 -124 104 -279 194 -334 194 -23 0 -41 -51 -62 -175 -16 -96 -12 -103 81 -126 83 -21 185 -67 300 -134 41 -24 118 -60 170 -80 52 -20 113 -47 135 -60 93 -54 180 -182 209 -304 8 -33 17 -64 21 -70 4 -7 72 -11 200 -11 219 0 198 -9 210 90 10 77 60 231 105 320 122 241 339 440 633 581 60 28 111 58 114 66 10 25 -78 187 -104 191 -14 2 -55 -24 -130 -84 -155 -125 -204 -149 -219 -109 -16 39 61 187 158 306 68 84 68 84 -24 160 -91 75 -105 80 -134 44 -73 -90 -138 -252 -198 -490 -24 -95 -60 -211 -79 -257 -77 -191 -251 -441 -325 -469 -90 -35 -154 173 -104 338 28 91 81 171 202 304 121 133 179 229 224 369 54 167 84 341 62 359 -35 29 -199 61 -211 41 -4 -7 -11 -67 -16 -134 -12 -185 -46 -296 -102 -330 -41 -25 -60 53 -78 304 -6 85 -16 161 -21 168 -13 15 -41 14 -137 -3z M2640 1847 c-341 -121 -578 -377 -645 -698 -26 -122 -64 -109 315 -109 l330 0 19 31 c33 53 92 247 89 287 l-3 37 -75 -1 c-70 -2 -81 -5 -206 -69 -148 -75 -187 -81 -192 -32 -14 119 253 306 439 307 68 0 74 13 61 132 -17 156 -16 156 -132 115z M495 1478 c-14 -37 56 -301 106 -400 l19 -38 300 0 c331 0 315 -4 279 65 -38 72 -107 133 -204 179 -49 24 -119 65 -155 91 -100 74 -182 109 -267 113 -57 3 -74 1 -78 -10z";

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

/** The coral geometry, paint inherited from the parent (`fill` cascades into the path). */
export function CoralPaths() {
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
  /** Rendered HEIGHT in px (width = height x GLYPH_ASPECT). Default 20. */
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
