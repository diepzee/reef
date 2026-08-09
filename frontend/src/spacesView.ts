/**
 * The Spaces screen's persisted view preference (spec: "Main screen").
 * localStorage-backed so it survives reloads without a server round-trip;
 * unknown/absent values fall back to the default rather than throwing.
 * Accessed via `window.localStorage` (not the bare global) so the module
 * works unchanged under the jsdom test environment, which installs
 * `window` but no top-level `localStorage` binding.
 */

export type SpacesView = "list" | "grid";

const KEY = "reef.spacesView";

/** Read the persisted view, defaulting to `"list"` for absent or junk values. */
export function getSpacesView(): SpacesView {
  try {
    return window.localStorage.getItem(KEY) === "grid" ? "grid" : "list";
  } catch {
    return "list";
  }
}

/** Persist the chosen view. Storage failures (private mode) are non-fatal. */
export function setSpacesView(view: SpacesView): void {
  try {
    window.localStorage.setItem(KEY, view);
  } catch {
    // Preference simply won't stick — acceptable.
  }
}
