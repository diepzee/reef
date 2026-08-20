/**
 * The Coves screen's persisted view preference (spec: "Main screen").
 * localStorage-backed so it survives reloads without a server round-trip;
 * unknown/absent values fall back to the default rather than throwing.
 * Accessed via `window.localStorage` (not the bare global) so the module
 * works unchanged under the jsdom test environment, which installs
 * `window` but no top-level `localStorage` binding.
 */

export type CovesView = "list" | "grid" | "graph";

const KEY = "reef.covesView";

/** Read the persisted view, defaulting to `"list"` for absent or junk values. */
export function getCovesView(): CovesView {
  try {
    const stored = window.localStorage.getItem(KEY);
    return stored === "grid" || stored === "graph" ? stored : "list";
  } catch {
    return "list";
  }
}

/** Persist the chosen view. Storage failures (private mode) are non-fatal. */
export function setCovesView(view: CovesView): void {
  try {
    window.localStorage.setItem(KEY, view);
  } catch {
    // Preference simply won't stick — acceptable.
  }
}
