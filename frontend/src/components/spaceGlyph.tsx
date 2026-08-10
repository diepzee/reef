/**
 * A space's own reef organism, procedurally grown from its alias — the
 * rendering half of `organisms.ts` (which owns the deterministic
 * alias → family → parameters pipeline; spec:
 * docs/superpowers/specs/2026-08-10-procedural-reef-organisms-design.md).
 *
 * The organism box is square (64-box): grounded families stand on the
 * y=54 baseline, radial families center on (32, 32). Callers size with a
 * single `size` — width and height are equal.
 */

import { organismFor, type OrganismPath } from "./organisms";

/** Renders one OrganismPath — filled silhouette or thick round-capped stroke. */
function OrgPath({ p }: { p: OrganismPath }) {
  return p.stroke !== undefined ? (
    <path
      d={p.d}
      fill="none"
      stroke="currentColor"
      strokeWidth={p.stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ) : (
    <path d={p.d} fill="currentColor" fillRule={p.evenodd ? "evenodd" : "nonzero"} />
  );
}

interface SpaceGlyphProps {
  /** The space's alias — decides which organism grows. */
  alias: string;
  /** Glyph color — typically the space's hue (`spaceColor(alias)`). */
  color: string;
  /** Rendered width AND height in px — the organism box is square. Default 20. */
  size?: number;
}

/** A space's own procedurally grown reef organism, single-colored. */
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
