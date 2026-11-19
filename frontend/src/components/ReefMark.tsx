/**
 * The reef mark as inline SVG, mirroring `public/reef.svg`'s brain coral —
 * a compact, branching brain-coral silhouette vectorized from the approved reference.
 * The mark has no baked background: the PNG, favicon SVG, and inline glyph all
 * remain transparent and take their surrounding surface from the caller.
 *
 * The gradient is userSpaceOnUse in the trace's source coordinates:
 * objectBoundingBox units degenerate on axis-aligned subpaths, and
 * user-space coords are safe for any geometry drawn in this box.
 */

import { useId } from "react";

/** Potrace path of the brain coral, in the traced source's pixel space. */
const BRAIN_D =
  "M4227 8649 c-339 -37 -649 -202 -739 -395 -70 -150 -31 -283 158 -544 164 -226 220 -358 231 -546 19 -310 -165 -716 -371 -819 -64 -32 -149 -34 -205 -5 -149 75 -171 286 -67 630 56 183 86 309 95 398 35 337 -129 574 -398 574 -140 0 -277 -64 -412 -190 -170 -159 -286 -362 -354 -615 -30 -110 -49 -132 -155 -177 -97 -42 -180 -102 -259 -186 -200 -213 -309 -547 -246 -754 16 -54 29 -75 80 -126 96 -97 171 -116 575 -144 235 -17 462 -61 595 -117 119 -50 151 -66 231 -118 174 -113 307 -270 448 -526 238 -435 314 -540 560 -779 474 -460 527 -520 616 -704 95 -195 103 -416 14 -416 -14 0 -34 5 -44 10 -11 6 -73 72 -138 148 -212 244 -389 404 -593 534 -124 80 -215 127 -432 226 -339 155 -469 237 -646 407 -157 151 -260 286 -391 510 -201 342 -361 475 -574 475 -112 0 -256 -78 -328 -176 -78 -107 -107 -197 -115 -349 -22 -454 325 -1054 737 -1273 171 -91 339 -132 667 -162 257 -24 396 -48 540 -95 424 -139 812 -479 963 -844 49 -120 71 -238 79 -435 7 -151 12 -191 31 -239 45 -118 153 -216 280 -256 119 -37 197 -43 509 -38 267 4 303 6 369 26 105 31 168 65 228 125 92 93 124 179 124 341 0 226 35 386 119 553 86 170 205 316 363 448 309 257 604 371 1058 410 402 33 617 93 810 224 287 195 561 634 624 999 61 359 -65 642 -322 721 -151 47 -303 4 -438 -122 -78 -73 -121 -133 -239 -328 -207 -342 -368 -526 -605 -689 -110 -76 -233 -141 -450 -239 -456 -205 -685 -376 -1007 -749 -125 -146 -145 -163 -185 -163 -49 0 -61 34 -56 153 4 81 12 119 36 186 77 206 176 333 502 639 263 248 400 402 512 577 25 39 93 158 153 265 130 234 122 223 207 337 230 310 569 463 1113 503 344 25 413 38 510 94 87 51 145 147 161 263 13 101 -34 292 -108 438 -102 201 -238 335 -428 420 -81 35 -125 79 -134 131 -13 72 -67 226 -108 308 -59 117 -122 209 -212 303 -203 215 -429 287 -621 199 -98 -45 -166 -124 -212 -251 -15 -41 -18 -79 -18 -205 1 -163 12 -221 85 -455 113 -358 93 -577 -61 -656 -50 -25 -140 -25 -197 1 -54 24 -141 107 -189 178 -45 69 -115 210 -136 277 -53 167 -67 377 -33 515 27 111 93 237 207 395 118 164 152 225 181 318 57 188 -56 369 -307 495 -90 45 -258 99 -365 118 -263 46 -476 1 -617 -130 -68 -64 -113 -134 -148 -233 -78 -224 -57 -423 105 -1013 87 -314 217 -701 395 -1170 170 -449 190 -536 189 -805 0 -172 -3 -199 -27 -287 -48 -180 -169 -405 -323 -598 -86 -108 -304 -326 -368 -368 -85 -55 -163 -36 -186 46 -36 132 11 300 194 684 151 318 179 399 180 523 0 76 -3 97 -23 135 -39 75 -62 101 -122 140 -146 94 -331 70 -435 -58 -51 -61 -77 -129 -99 -252 -49 -286 -142 -420 -291 -420 -100 0 -198 70 -254 180 -50 100 -63 175 -56 335 9 241 41 350 306 1045 147 385 322 968 396 1320 34 161 42 348 20 460 -69 342 -331 519 -709 479z";

/**
 * Transforms mapping the traced path into the 64-box: outer fits the mark with
 * its base on y=52; inner is Potrace's source-space flip. Copied from reef.svg
 * — keep in sync.
 */
const OUTER_TRANSFORM = "translate(10.894708,12) scale(0.05615348)";
const INNER_TRANSFORM =
  "translate(-136.216577,865.448541) scale(0.1,-0.1)";

/** A close crop with breathing room around every edge of the traced mark. */
const GLYPH_VIEWBOX = "9.5 10.5 45 43";
/** Width/height ratio of {@link GLYPH_VIEWBOX}. */
export const GLYPH_ASPECT = 45 / 43;

/** The brain-coral geometry; paint is inherited from the parent. */
export function BrainPaths() {
  return (
    <g transform={OUTER_TRANSFORM}>
      <g transform={INNER_TRANSFORM}>
        <path d={BRAIN_D} />
      </g>
    </g>
  );
}

interface ReefMarkProps {
  /** Rendered width/height in px — the transparent viewBox is square. Default 30. */
  size?: number;
  /** Extra class applied to the root `<svg>`, for layout hooks. */
  className?: string;
}

/** The full transparent reef mark with its seafoam-to-teal gradient. */
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
          y1="0"
          x2="0"
          y2="8700"
        >
          <stop offset="0" stopColor="#0d9488" />
          <stop offset="1" stopColor="#5eead4" />
        </linearGradient>
      </defs>
      <g fill={`url(#${gradientId})`}>
        <BrainPaths />
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
 * The mark alone — a single-color glyph for space chips and brand lockups.
 * The padded crop keeps the negative-space branches open at small sizes and
 * prevents animation or rasterisation from shaving an outside edge.
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
        <BrainPaths />
      </g>
    </svg>
  );
}
