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
 */

import { useState } from "react";
import { Link, useLocation } from "react-router-dom";

import reefIcon from "../../public/reef.svg";
import { apiSend } from "../api";
import { useIndex } from "../IndexProvider";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";
import type { Me } from "../types";
import { Avatar, AvatarStack } from "./Avatar";

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

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await apiSend("POST", "/api/auth/logout");
      window.location.href = "/app";
    } catch {
      setSigningOut(false);
    }
  }

  return (
    <nav className="side">
      <Link to="/" className="side-brand">
        <img src={reefIcon} alt="" className="side-brand-icon" width="22" height="22" />
        rif
      </Link>

      <div className="side-label">Spaces</div>

      {index?.spaces.map((space) => {
        const isActive = space.alias === activeSpace;
        const isPersonal = space.alias === "personal";

        return (
          <div key={space.alias}>
            <Link
              to={`/s/${space.alias}`}
              className={`side-item ${isActive ? "active" : ""}`}
            >
              <span className="side-dot" />
              <span>{isPersonal ? "Personal" : space.alias}</span>
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
                    />
                  </span>
                )
              ) : (
                <span className="side-count">{space.pages.length}</span>
              )}
            </Link>

            {isActive && (
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

      <Link to="/spaces/new" className="side-item side-newspace">
        <span className="side-dot" />
        <span>＋ New space</span>
      </Link>

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
