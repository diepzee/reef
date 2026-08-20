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
 * Also owns the single shared `MembersSheet` instance: the cove header
 * (`CoveView`'s whobar), the sidebar's active-cove avatar stack, and
 * (from Task 6) the page header all need to open "the" members sheet for
 * whatever cove they're showing, and there must only ever be one sheet
 * mounted at a time. `MembersSheetContext` (its own module — see
 * `useMembersSheet.ts` — so `Sidebar.tsx` can import the hook without an
 * `AppShell` <-> `Sidebar` circular import) hands every descendant an
 * `openMembers(cove)` callback; the sheet itself renders here, keyed by
 * the cove it's showing so switching coves resets its local state (the
 * v1 stale-disclosure lesson — see `MembersSheet`'s docstring). Closing
 * only flips `sheetOpen`, leaving `sheetCove` in place, so the sheet's
 * content stays put while it animates away instead of vanishing.
 *
 * The mobile header's brand row also carries the identity pass's serif
 * tagline, but only while Home (`/`) is the routed view — every other
 * mobile screen keeps the plain brand row, matching the spec's "Home gets
 * the serif tagline ... under the brand row".
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Link, useLocation } from "react-router-dom";

import { ApiError, apiGet, apiSend } from "../api";
import type { Me, ReleaseNotesFeed } from "../types";
import {
  AppearanceContext,
  type AppearanceMap,
  type CoveAppearance,
} from "../useAppearance";
import { MeContext } from "../useMe";
import { useMediaQuery } from "../useMediaQuery";
import { MembersSheetContext } from "../useMembersSheet";
import { ReleaseNotesContext } from "../useReleaseNotes";
import { AccountMenu } from "./AccountMenu";
import { MembersSheet } from "./MembersSheet";
import { FrondGlyph } from "./ReefMark";
import { ReleaseNotes } from "./ReleaseNotes";
import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const isDesktop = useMediaQuery("(min-width: 900px)");
  const location = useLocation();
  const isHome = location.pathname === "/";
  const [me, setMe] = useState<Me | null>(null);

  const [sheetCove, setSheetCove] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const openMembers = useCallback((cove: string) => {
    setSheetCove(cove);
    setSheetOpen(true);
  }, []);
  const closeMembers = useCallback(() => setSheetOpen(false), []);
  const sheetContextValue = useMemo(() => ({ openMembers }), [openMembers]);

  const [feed, setFeed] = useState<ReleaseNotesFeed | null>(null);
  const [releaseNotesOpen, setReleaseNotesOpen] = useState(false);

  const openReleaseNotes = useCallback(() => {
    setReleaseNotesOpen(true);
    // Opening *is* reading, so the mark moves now rather than on close: a
    // reader who navigates away mid-panel has still seen it, and coming
    // back to the same dot would read as the app having lost the fact.
    apiSend("POST", "/api/release-notes/seen")
      .then(() => {
        setFeed((current) => (current ? { ...current, unread: false } : current));
      })
      .catch(() => {
        // The dot staying lit is the whole cost of a failed stamp, and the
        // next open tries again. Not worth an error surface.
      });
  }, []);

  const releaseNotesContextValue = useMemo(
    () => ({ unread: feed?.unread ?? false, openReleaseNotes }),
    [feed?.unread, openReleaseNotes],
  );

  const setAvatar = useCallback((avatar: string | null) => {
    setMe((current) => (current ? { ...current, avatar } : current));
  }, []);
  const meContextValue = useMemo(() => ({ me, setAvatar }), [me, setAvatar]);

  const [appearance, setAppearanceMap] = useState<AppearanceMap>({});
  const setAppearance = useCallback((alias: string, look: CoveAppearance) => {
    setAppearanceMap((current) => ({ ...current, [alias]: look }));
  }, []);
  const appearanceContextValue = useMemo(
    () => ({ appearance, setAppearance }),
    [appearance, setAppearance],
  );

  useEffect(() => {
    let cancelled = false;
    apiGet<{ coves: AppearanceMap }>("/api/appearance")
      .then((payload) => {
        if (!cancelled) setAppearanceMap(payload.coves);
      })
      .catch(() => {
        // Every cove already has a derived look, so a failure here costs
        // nothing but the viewer's overrides — not worth a error surface.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiGet<ReleaseNotesFeed>("/api/release-notes")
      .then((payload) => {
        if (!cancelled) setFeed(payload);
      })
      .catch(() => {
        // The panel is not load-bearing: a failure here costs the reader a
        // list of changes, and must not cost them the app.
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  const sheet = sheetCove !== null && (
    <MembersSheet
      key={sheetCove}
      cove={sheetCove}
      open={sheetOpen}
      onClose={closeMembers}
    />
  );

  return (
    <MeContext.Provider value={meContextValue}>
      <AppearanceContext.Provider value={appearanceContextValue}>
        <MembersSheetContext.Provider value={sheetContextValue}>
          <ReleaseNotesContext.Provider value={releaseNotesContextValue}>
            {isDesktop ? (
              <div className="shell">
                <Sidebar me={me} />
                <div className="content">
                  <div className="app-stack">{children}</div>
                </div>
              </div>
            ) : (
              <div className="app-stack">
                <header className="app-header">
                  <Link to="/" className="app-header-link">
                    <FrondGlyph color="var(--accent)" size={17} />
                    <span className="app-header-wordmark">reef</span>
                  </Link>
                  {/* Without this the phone has no account surface at all —
                    no profile, and no way to sign out. */}
                  <AccountMenu me={me} placement="down" />
                  {isHome && (
                    <p className="app-header-tagline">
                      memories you grow together
                    </p>
                  )}
                </header>
                {children}
              </div>
            )}
            {sheet}
            {releaseNotesOpen && (
              <ReleaseNotes
                entries={feed?.entries ?? []}
                onClose={() => setReleaseNotesOpen(false)}
              />
            )}
          </ReleaseNotesContext.Provider>
        </MembersSheetContext.Provider>
      </AppearanceContext.Provider>
    </MeContext.Provider>
  );
}
