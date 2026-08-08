/**
 * The app's top-level chrome: a two-pane desktop shell at `≥900px`
 * (persistent `Sidebar` + scrollable content pane), or today's stacked
 * mobile header above the routed content below that width.
 *
 * Fetches the signed-in person once on mount and passes it down to
 * `Sidebar`'s account row — the mobile header doesn't need it, so the
 * fetch result is simply unused on that branch rather than skipped, since
 * a resize across the breakpoint must not trigger a fresh fetch.
 */

import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import reefIcon from "../../public/reef.svg";
import { ApiError, apiGet } from "../api";
import type { Me } from "../types";
import { Sidebar } from "./Sidebar";

/**
 * Tracks whether a CSS media query currently matches, updating live via
 * `MediaQueryList`'s `change` event as the viewport crosses the breakpoint.
 *
 * :param query: a media query string, e.g. ``"(min-width: 900px)"``
 * :returns: whether `query` currently matches
 */
function useMediaQuery(query: string): boolean {
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

export function AppShell({ children }: { children: ReactNode }) {
  const isDesktop = useMediaQuery("(min-width: 900px)");
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGet<Me>("/api/me")
      .then((payload) => {
        if (!cancelled) setMe(payload);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // A 401 is already being handled by apiGet's redirect to the login
        // route; any other failure just leaves the account row blank —
        // there's no dedicated error surface for the shell chrome itself.
        if (err instanceof ApiError && err.status === 401) return;
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (isDesktop) {
    return (
      <div className="shell">
        <Sidebar me={me} />
        <div className="content">
          <div className="app-stack">{children}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-stack">
      <header className="app-header">
        <Link to="/" className="app-header-link">
          <img
            src={reefIcon}
            alt="rif"
            className="app-header-icon"
            width="26"
            height="26"
          />
          <span className="app-header-wordmark">rif</span>
        </Link>
      </header>
      {children}
    </div>
  );
}
