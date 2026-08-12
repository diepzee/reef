/**
 * Which coves the reader has folded open or shut in the sidebar, by alias.
 *
 * Only deliberate choices are stored. A cove the reader has never touched is
 * absent from the map and falls back to "open if it is the cove they are in",
 * which is how the pane behaved before it could fold at all. That fallback is
 * why this cannot be a plain list of open aliases: "never said" and "said
 * shut" have to stay distinguishable, or navigating into a cove would
 * silently re-open one the reader had just closed.
 *
 * localStorage-backed so a fold survives a reload without a server
 * round-trip, and reached through `window.localStorage` (not the bare
 * global) so the module works unchanged under jsdom, which installs `window`
 * but no top-level `localStorage` binding — same discipline as `spacesView`.
 */

export type CoveFolds = Record<string, boolean>;

const KEY = "reef.sidebar.openCoves";

/**
 * Read the stored folds, discarding anything malformed.
 *
 * :returns: alias -> open, containing only entries the reader actually set
 */
export function getCoveFolds(): CoveFolds {
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    // An array is `typeof "object"` too, and would yield index-keyed junk.
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const entries = Object.entries(parsed as Record<string, unknown>);
    return Object.fromEntries(
      entries.filter((entry): entry is [string, boolean] => typeof entry[1] === "boolean"),
    );
  } catch {
    // Reading storage throws outright in private-mode Safari, and a
    // half-written value throws in the parse. Neither is worth failing the
    // whole shell over — the reader just gets the default folds.
    return {};
  }
}

/**
 * Record one cove's fold, leaving every other cove's choice untouched.
 *
 * :param folds: the current map, treated as immutable
 * :param alias: the cove being folded
 * :param open: whether it is now open
 * :returns: the updated map, which the caller renders from
 */
export function setCoveFold(folds: CoveFolds, alias: string, open: boolean): CoveFolds {
  const next = { ...folds, [alias]: open };
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage being unavailable costs persistence across reloads, not the
    // fold itself — that lives in component state either way.
  }
  return next;
}

/**
 * Whether a cove's pages should show.
 *
 * :param folds: the reader's recorded choices
 * :param alias: the cove in question
 * :param isActive: whether this is the cove currently being viewed
 * :param hasPages: whether it has any pages to show
 */
export function isCoveOpen(
  folds: CoveFolds,
  alias: string,
  isActive: boolean,
  hasPages: boolean,
): boolean {
  if (!hasPages) return false;
  return folds[alias] ?? isActive;
}
