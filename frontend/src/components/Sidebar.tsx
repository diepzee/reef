/**
 * The desktop shell's persistent left pane: brand, every space the
 * principal can see, the active space's pages nested under it, "new page"
 * / "new space" actions, and an account row with sign-out.
 *
 * Active state comes from the route rather than local state — `useParams`
 * only works inside a matched `<Route>`, and `Sidebar` renders alongside
 * `<Routes>` in `AppShell`, not inside one — so this parses the current
 * location's `/s/<alias>` (and `/s/<alias>/p/<path>`) prefixes instead.
 *
 * Per the brief's documented narrowing: showing every space row's member
 * stack would be an N+1 fetch (one `useMembers` call per space). Only the
 * *active* space's stack is fetched and shown; every other space row (and
 * the personal space, which has no membership to administer) shows its
 * page count instead.
 *
 * Each space row's dot is tinted with that space's `spaceColor(alias).base`
 * (the identity pass's per-space hue), same discipline `Home`'s cards and
 * `PageView`'s page-bar dot use — so a space's color reads consistently
 * everywhere it appears.
 */

import { useCallback, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { apiSend } from "../api";
import { getCoveFolds, isCoveOpen, setCoveFold } from "../coveFolds";
import { useIndex } from "../IndexProvider";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";
import type { Me } from "../types";
import { Avatar, AvatarStack } from "./Avatar";
import { FrondGlyph } from "./ReefMark";
import { spaceColor } from "./spaceColor";

/** Parse the active space alias and (if on a page route) active page path from a pathname. */
function parseLocation(pathname: string): { space: string | null; page: string | null } {
  const decoded = decodeURIComponent(pathname);
  const spaceMatch = decoded.match(/^\/s\/([^/]+)/);
  const pageMatch = decoded.match(/^\/s\/[^/]+\/p\/(.*)$/);
  return {
    space: spaceMatch?.[1] ?? null,
    page: pageMatch?.[1] ?? null,
  };
}


export function Sidebar({ me }: { me: Me | null }) {
  const { index } = useIndex();
  const location = useLocation();
  const { space: activeSpace, page: activePage } = parseLocation(location.pathname);

  const { members } = useMembers(activeSpace);
  const { openMembers } = useMembersSheet();
  const [signingOut, setSigningOut] = useState(false);
  const [folds, setFolds] = useState(getCoveFolds);

  const toggleFold = useCallback((alias: string, open: boolean) => {
    setFolds((previous) => setCoveFold(previous, alias, open));
  }, []);

  async function handleSignOut() {
    setSigningOut(true);
    try {
      // The backend hands back a WorkOS logout URL when it knows the
      // upstream AuthKit session id; navigating there ends that session
      // too. Without it, only reef's cookie is gone and the next login
      // redirect would silently sign the user right back in.
      const result = await apiSend<{ ok: boolean; logout_url?: string }>(
        "POST",
        "/api/auth/logout",
      );
      window.location.href = result.logout_url ?? "/app/signed-out";
    } catch {
      setSigningOut(false);
    }
  }

  return (
    <nav className="side">
      <Link to="/" className="side-brand">
        <FrondGlyph color="var(--accent)" size={15} />
        reef
      </Link>

      <Link
        to="/index"
        className={`side-item ${location.pathname === "/index" ? "active" : ""}`}
      >
        <svg className="side-index-icon" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M2 2.5h8M2 6h8M2 9.5h8" />
        </svg>
        <span>Index</span>
      </Link>

      <Link
        to="/export"
        className={`side-item ${location.pathname === "/export" ? "active" : ""}`}
      >
        <svg className="side-index-icon" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M6 1.5v6M3.5 5 6 7.5 8.5 5M2 10h8" />
        </svg>
        <span>Export</span>
      </Link>

      {/* Names what the list holds rather than repeating the wordmark two
          rows below it — and in small-caps --faint, which is not how the
          brand is set anywhere else. */}
      <div className="side-label">Coves</div>

      {index?.spaces.map((space) => {
        const isActive = space.alias === activeSpace;
        const isPersonal = space.alias === "personal";
        const hue = spaceColor(space.alias);
        const hasPages = space.pages.length > 0;
        const isOpen = isCoveOpen(folds, space.alias, isActive, hasPages);

        return (
          <div key={space.alias}>
            {/*
              The twisty is a sibling of the row's link, not a child of it:
              a <button> inside an <a> is invalid, and the nested-interactive
              dodge the member stack below has to pull (preventDefault plus
              stopPropagation, or the anchor hard-navigates) is worth avoiding
              on a control the reader will hit far more often. The wrapper
              carries the active tint so it still spans the whole row.
            */}
            <div className={`side-row ${isActive ? "active" : ""}`}>
              {hasPages ? (
                <button
                  type="button"
                  className="side-twist"
                  aria-expanded={isOpen}
                  aria-label={`${isOpen ? "Collapse" : "Expand"} ${space.alias}`}
                  onClick={() => toggleFold(space.alias, !isOpen)}
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                    <path d="M4.5 2.5 8 6l-3.5 3.5" />
                  </svg>
                </button>
              ) : (
                // An empty cove has nothing to fold, but its name still has to
                // line up with the ones that do.
                <span className="side-twist side-twist-blank" aria-hidden="true" />
              )}
              <Link to={`/s/${space.alias}`} className="side-item">
                <span className="side-dot" style={{ background: hue.base }} />
                <span>{space.alias}</span>
                {isActive && !isPersonal ? (
                  members && (
                  // This sits inside the space's own <Link>, and opening
                  // the sheet should not also navigate. preventDefault is
                  // required alongside stopPropagation: stopPropagation
                  // alone stops the event from ever reaching the Link's
                  // own onClick (the one that would normally call
                  // preventDefault + client-side navigate), which leaves
                  // the browser's native anchor-click behavior
                  // unprevented — the tab hard-reloads the href instead,
                  // wiping all React state including the sheet that had
                  // just opened. Confirmed live: without preventDefault
                  // here, clicking this stack opened the sheet for one
                  // render and then a full page reload silently closed it.
                    <span
                      className="side-item-right"
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                      }}
                    >
                      <AvatarStack
                        names={members.members.map((member) => member.display_name)}
                        size="sm"
                        onClick={() => openMembers(space.alias)}
                        ariaLabel={`Members of ${space.alias}`}
                      />
                    </span>
                  )
                ) : (
                  <span className="side-count">{space.pages.length}</span>
                )}
              </Link>
            </div>

            {isOpen && (
              <>
                {space.pages.map((page) => (
                  <Link
                    key={page.path}
                    to={`/s/${space.alias}/p/${page.path}`}
                    className={`side-page ${activePage === page.path ? "active" : ""}`}
                  >
                    {page.title || page.path}
                  </Link>
                ))}
                <Link to={`/s/${space.alias}/new`} className="side-newpage">
                  ＋ New page
                </Link>
              </>
            )}
          </div>
        );
      })}

      {/*
        Both of these used to wear a `side-dot` and sit in the cove list, which
        made two actions read as two more coves — the dot is the mark of a cove
        in this pane, so lending it to a link that is not one is a lie. "New
        cove" stays with the list because that is what it adds to. Inviting
        someone to reef adds a person to the product rather than to any cove,
        so it goes below with the account, behind a rule.
      */}
      <Link to="/spaces/new" className="side-newcove">
        ＋ New cove
      </Link>

      <div className="side-foot">
        <Link to="/invite" className="side-foot-link">
          Invite someone to <span className="reef-name">reef</span>
        </Link>
      </div>

      <div className="side-me">
        {me && <Avatar name={me.display_name} size="sm" />}
        <span className="side-me-name">{me?.display_name ?? ""}</span>
        <span aria-hidden="true"> · </span>
        <button
          type="button"
          className="side-signout"
          disabled={signingOut}
          onClick={handleSignOut}
        >
          Sign out
        </button>
      </div>
    </nav>
  );
}
