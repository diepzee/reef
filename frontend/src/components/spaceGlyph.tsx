/**
 * A space's own reef organism, procedurally grown from its alias — the
 * rendering half of `organisms.ts` (which owns the deterministic
 * alias → family → parameters pipeline; spec:
 * docs/superpowers/specs/2026-08-10-procedural-reef-organisms-design.md).
 *
 * Families are drawn wherever suits them in the 64-box — grounded ones
 * stand on the y=54 baseline, radial ones center on (32, 32) — and they
 * differ in size by more than twofold. Shown through a fixed `0 0 64 64`
 * window they therefore arrive at wildly different visual weights, some
 * hugging the bottom edge. So the glyph measures what it actually drew
 * and frames that: every organism reads at one height, centered on its
 * own mass, whichever family it belongs to.
 */

import { useLayoutEffect, useRef, useState } from "react";

import { organismFor, type OrganismPath } from "./organisms";

/**
 * Share of the box an organism's height should fill.
 *
 * Chosen against the corpus: individuals run up to about 1.5 times as wide as they are tall
 * at the 98th percentile, and 0.64 x 1.5 clears `MAX_WIDTH`. So all but the broadest ~2%
 * land on exactly this height, and even those stay close.
 */
const TARGET_HEIGHT = 0.64;
/** Ceiling on width, so a broad organism is reined in rather than clipped. */
const MAX_WIDTH = 0.98;
/** Until measured, the box the families are drawn in. */
const UNMEASURED = "0 0 64 64";

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

/**
 * The square window that puts `group`'s drawn geometry at a common height, centered.
 *
 * :param group: the rendered organism, already in the DOM
 * :param strokePad: half the widest stroke, which ``getBBox`` does not account for
 * :returns: a ``viewBox`` string
 */
function frame(group: SVGGElement, strokePad: number): string {
  const box = group.getBBox();
  const width = box.width + 2 * strokePad;
  const height = box.height + 2 * strokePad;
  if (width <= 0 || height <= 0) return UNMEASURED;
  // Sizing on height alone is what makes the set look even; the width term only ever
  // enlarges the window, and so only bites for an unusually broad individual.
  const side = Math.max(height / TARGET_HEIGHT, width / MAX_WIDTH);
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  return `${cx - side / 2} ${cy - side / 2} ${side} ${side}`;
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
  const group = useRef<SVGGElement>(null);
  const [viewBox, setViewBox] = useState(UNMEASURED);

  // Measured before paint, so the glyph is never briefly shown unframed.
  useLayoutEffect(() => {
    if (!group.current) return;
    const strokePad = organism.paths.reduce((w, p) => Math.max(w, p.stroke ?? 0), 0) / 2;
    setViewBox(frame(group.current, strokePad));
  }, [alias]);

  return (
    <svg viewBox={viewBox} width={size} height={size} style={{ color }} aria-hidden="true">
      <g ref={group}>
        {organism.paths.map((p, i) => (
          <OrgPath key={i} p={p} />
        ))}
      </g>
    </svg>
  );
}
