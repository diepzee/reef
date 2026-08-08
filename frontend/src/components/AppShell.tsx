/**
 * The app's top-level chrome: a two-pane desktop shell at `≥900px`
 * (persistent `Sidebar` + scrollable content pane), or today's stacked
 * mobile header above the routed content below that width.
 *
 * Fetches the signed-in person once on mount and passes it down to
 * `Sidebar`'s account row — the mobile header doesn't need it, so the
 * fetch result is simply unused on that branch rather than skipped, since
 * a resize across the breakpoint must not trigger a fresh fetch.
 *
 * Also owns the single shared `MembersSheet` instance: the space header
 * (`SpaceView`'s whobar), the sidebar's active-space avatar stack, and
 * (from Task 6) the page header all need to open "the" members sheet for
 * whatever space they're showing, and there must only ever be one sheet
 * mounted at a time. `MembersSheetContext` (its own module — see
 * `useMembersSheet.ts` — so `Sidebar.tsx` can import the hook without an
 * `AppShell` <-> `Sidebar` circular import) hands every descendant an
 * `openMembers(space)` callback; the sheet itself renders here, keyed by
 * the space it's showing so switching spaces resets its local state (the
 * v1 stale-disclosure lesson — see `MembersSheet`'s docstring). Closing
 * only flips `sheetOpen`, leaving `sheetSpace` in place, so the sheet's
 * content stays put while it animates away instead of vanishing.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import reefIcon from "../../public/reef.svg";
import { ApiError, apiGet } from "../api";
import type { Me } from "../types";
import { useMediaQuery } from "../useMediaQuery";
import { MembersSheetContext } from "../useMembersSheet";
import { MembersSheet } from "./MembersSheet";
import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const isDesktop = useMediaQuery("(min-width: 900px)");
  const [me, setMe] = useState<Me | null>(null);

  const [sheetSpace, setSheetSpace] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const openMembers = useCallback((space: string) => {
    setSheetSpace(space);
    setSheetOpen(true);
  }, []);
  const closeMembers = useCallback(() => setSheetOpen(false), []);
  const sheetContextValue = useMemo(() => ({ openMembers }), [openMembers]);

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

  const sheet = sheetSpace !== null && (
    <MembersSheet key={sheetSpace} space={sheetSpace} open={sheetOpen} onClose={closeMembers} />
  );

  if (isDesktop) {
    return (
      <MembersSheetContext.Provider value={sheetContextValue}>
        <div className="shell">
          <Sidebar me={me} />
          <div className="content">
            <div className="app-stack">{children}</div>
          </div>
        </div>
        {sheet}
      </MembersSheetContext.Provider>
    );
  }

  return (
    <MembersSheetContext.Provider value={sheetContextValue}>
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
      {sheet}
    </MembersSheetContext.Provider>
  );
}
