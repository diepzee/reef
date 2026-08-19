/**
 * Client-side mirror of `_summary` in `src/reef/context.py:63`.
 *
 * Kept in sync deliberately, not shared: the server computes the same
 * description for `PageMeta.description`, but the editor (Task 10) needs
 * to preview it client-side as the author types, before any round trip.
 */

/**
 * Return a page's one-line description: its first prose line.
 *
 * The page style mandates a short summary as the opening paragraph, so the
 * first non-heading line is the curated description, not an arbitrary
 * excerpt.
 *
 * :param body: the page's markdown body
 * :returns: the first prose line, trimmed to 200 characters, or ``""`` if
 *     the body has no such line
 */
export function indexDescription(body: string): string {
  for (const line of body.split("\n")) {
    const stripped = line.trim();
    if (!stripped || stripped.startsWith("#")) {
      continue;
    }
    return stripped.slice(0, 200);
  }
  return "";
}
