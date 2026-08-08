/**
 * 8-color deterministic palette for avatars by name.
 */
const palette = [
  "#0d9488",
  "#6366f1",
  "#f59e0b",
  "#ec4899",
  "#0284c7",
  "#84cc16",
  "#8b5cf6",
  "#f97316",
] as const;

/**
 * Pick a deterministic color from the palette based on name's character codes.
 */
export function avatarColor(name: string): string {
  let sum = 0;
  for (const char of name) {
    sum += char.charCodeAt(0);
  }
  return palette[sum % palette.length]!;
}

/**
 * First grapheme of the name, uppercased; "?" if empty.
 */
export function initialOf(name: string): string {
  if (!name) return "?";
  const first = Array.from(name)[0];
  return first ? first.toUpperCase() : "?";
}
