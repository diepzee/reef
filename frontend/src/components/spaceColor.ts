/**
 * Per-space hue pairs for the identity pass — a `{base, light}` gradient
 * pair used for the home-card stripe/chip, the sidebar space dot, and the
 * page bar's hue dot (see spec "Identity pass (rev 2)").
 */

/** A space's hue: a deep `base` and a lighter `light` for gradients/tints. */
export interface SpaceHue {
  base: string;
  light: string;
}

/** `personal` is always seafoam, never hashed — it's the one space every principal shares. */
const PERSONAL: SpaceHue = { base: "#0d9488", light: "#5eead4" };

/** The seven-pair sea palette shared spaces hash across. */
const palette: readonly SpaceHue[] = [
  { base: "#f59e0b", light: "#fbbf24" }, // amber
  { base: "#6366f1", light: "#a5b4fc" }, // indigo
  { base: "#ec4899", light: "#f9a8d4" }, // pink
  { base: "#0284c7", light: "#7dd3fc" }, // sky
  { base: "#84cc16", light: "#bef264" }, // lime
  { base: "#8b5cf6", light: "#c4b5fd" }, // violet
  { base: "#f97316", light: "#fdba74" }, // orange
];

/**
 * Deterministic `{base, light}` hue pair for a space's alias.
 *
 * `"personal"` always returns the fixed seafoam pair. Every other alias
 * hashes across the seven-pair sea palette by summing character codes —
 * the same char-code-sum discipline `avatarColor` uses for names — so a
 * given alias always resolves to the same pair.
 *
 * :param alias: the space's alias, e.g. "personal" or "roadtrip"
 * :returns: the space's `{base, light}` hue pair
 */
export function spaceColor(alias: string): SpaceHue {
  if (alias === "personal") return PERSONAL;
  let sum = 0;
  for (const char of alias) {
    sum += char.charCodeAt(0);
  }
  return palette[sum % palette.length]!;
}
