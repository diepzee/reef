/**
 * Tracks whether a CSS media query currently matches, updating live via
 * `MediaQueryList`'s `change` event as the viewport crosses the breakpoint.
 *
 * Originally lived in `AppShell.tsx` (the only consumer at the time);
 * moved to its own module once `MembersSheet` needed the same
 * desktop/mobile check for its bottom-sheet-vs-right-panel layout — a
 * shared hook belongs in a shared module rather than being re-exported
 * from the component that happened to write it first.
 */

import { useEffect, useState } from "react";

/**
 * :param query: a media query string, e.g. ``"(min-width: 900px)"``
 * :returns: whether `query` currently matches
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);

  useEffect(() => {
    const mql = window.matchMedia(query);
    setMatches(mql.matches);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}
