/**
 * Per-cove hue pairs for the identity pass — a `{base, light}` gradient
 * pair used for the home-card stripe/chip, the sidebar cove dot, and the
 * page bar's hue dot (see spec "Identity pass (rev 2)").
 *
 * The hue is derived from the alias so every cove has one from the moment it
 * exists. A viewer who wants something else stores a *name* from this
 * palette (see `src/reef/appearance.py`, which validates against the same
 * list) and it is passed in as an override — the derivation stays the
 * fallback, so an unset or unrecognised name simply behaves as before.
 */

/** A cove's hue: a deep `base` and a lighter `light` for gradients/tints. */
export interface CoveHue {
  base: string;
  light: string;
}

/**
 * Every hue by name. Keep the keys in step with `COLORS` in
 * `src/reef/appearance.py`, which is what a stored choice is checked against.
 */
export const HUES = {
  seafoam: { base: "#0d9488", light: "#5eead4" },
  amber: { base: "#f59e0b", light: "#fbbf24" },
  indigo: { base: "#6366f1", light: "#a5b4fc" },
  pink: { base: "#ec4899", light: "#f9a8d4" },
  sky: { base: "#0284c7", light: "#7dd3fc" },
  lime: { base: "#84cc16", light: "#bef264" },
  violet: { base: "#8b5cf6", light: "#c4b5fd" },
  orange: { base: "#f97316", light: "#fdba74" },
} as const satisfies Record<string, CoveHue>;

/** A hue's name, as stored when somebody picks one. */
export type HueName = keyof typeof HUES;

/** The names, in the order a picker should offer them. */
export const HUE_NAMES = Object.keys(HUES) as HueName[];

/** `personal` is always seafoam, never hashed — it's the one cove every principal shares. */
const PERSONAL: CoveHue = HUES.seafoam;

/**
 * The seven-pair sea palette shared coves hash across.
 *
 * Order is load-bearing — the hash indexes into it — so entries may be
 * re-pointed but never reordered or removed without dealing every existing
 * cove a different colour.
 */
const palette: readonly CoveHue[] = [
  HUES.amber,
  HUES.indigo,
  HUES.pink,
  HUES.sky,
  HUES.lime,
  HUES.violet,
  HUES.orange,
];

/**
 * Deterministic `{base, light}` hue pair for a cove's alias, unless the
 * viewer has chosen one.
 *
 * `"personal"` always returns the fixed seafoam pair. Every other alias
 * hashes across the seven-pair sea palette by summing character codes —
 * the same char-code-sum discipline `avatarColor` uses for names — so a
 * given alias always resolves to the same pair.
 *
 * :param alias: the cove's alias, e.g. "personal" or "roadtrip"
 * :param chosen: a name from `HUES` the viewer picked, if any
 * :returns: the cove's `{base, light}` hue pair
 */
export function coveColor(alias: string, chosen?: string | null): CoveHue {
  if (chosen && chosen in HUES) return HUES[chosen as HueName];
  if (alias === "personal") return PERSONAL;
  let sum = 0;
  for (const char of alias) {
    sum += char.charCodeAt(0);
  }
  return palette[sum % palette.length]!;
}
