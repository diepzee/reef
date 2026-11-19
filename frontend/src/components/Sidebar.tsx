/**
 * The desktop shell's persistent left pane: brand, every space the
 * principal can see, the active space's engrams nested under it, "new engram"
 * / "new space" actions, and an account row with sign-out.
 *
 * Active state comes from the route rather than local state — `useParams`
 * only works inside a matched `<Route>`, and `Sidebar` renders alongside
 * `<Routes>` in `AppShell`, not inside one — so this parses the current
 * location's `/s/<alias>` (and `/s/<alias>/engram/<path>`) prefixes instead.
 *
 * Per the brief's documented narrowing: showing every space row's member
 * stack would be an N+1 fetch (one `useMembers` call per space). Only the
 * *active* space's stack is fetched and shown; every other space row (and
 * the personal space, which has no membership to administer) shows its
 * engram count instead.
 *
 * Each space row wears that space's own organism in its own hue, the same
 * mark `Home`'s cards and `EngramView`'s page bar carry — so a space is
 * recognized by its creature everywhere it appears, not just on the landing
 * screen. It replaced a plain tinted dot, which carried the hue but threw
 * away the half of a space's identity that is actually distinctive.
 */

import { useCallback, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { getSpaceFolds, isSpaceOpen, setSpaceFold } from "../spaceFolds";
import { useIndex } from "../IndexProvider";
import { useSpaceLook } from "../useAppearance";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";
import type { Me } from "../types";
import { AccountMenu } from "./AccountMenu";
import { AvatarStack } from "./Avatar";
import { FrondGlyph } from "./ReefMark";
import { SpaceGlyph } from "./spaceGlyph";

/** Parse the active space alias and (if on an engram route) active engram path from a pathname. */
function parseLocation(pathname: string): { space: string | null; engram: string | null } {
  const decoded = decodeURIComponent(pathname);
  const spaceMatch = decoded.match(/^\/s\/([^/]+)/);
  const engramMatch = decoded.match(/^\/s\/[^/]+\/engram\/(.*)$/);
  return {
    space: spaceMatch?.[1] ?? null,
    engram: engramMatch?.[1] ?? null,
  };
}


export function Sidebar({ me }: { me: Me | null }) {
  const { index } = useIndex();
  const location = useLocation();
  const { space: activeSpace, engram: activeEngram } = parseLocation(location.pathname);

  const { members } = useMembers(activeSpace);
  const { openMembers } = useMembersSheet();
  const [folds, setFolds] = useState(getSpaceFolds);
  const look = useSpaceLook();

  const toggleFold = useCallback((alias: string, open: boolean) => {
    setFolds((previous) => setSpaceFold(previous, alias, open));
  }, []);

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

      {/* Names what the list holds rather than repeating the wordmark two
          rows below it — and in small-caps --faint, which is not how the
          brand is set anywhere else. */}
      <div className="side-label">Spaces</div>

      {index?.spaces.map((space) => {
        const isActive = space.alias === activeSpace;
        const isPersonal = space.alias === "personal";
        const { hue, family } = look(space.alias);
        const hasEngrams = space.engrams.length > 0;
        const isOpen = isSpaceOpen(folds, space.alias, isActive, hasEngrams);

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
              {hasEngrams ? (
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
                // An empty space has nothing to fold, but its name still has to
                // line up with the ones that do.
                <span className="side-twist side-twist-blank" aria-hidden="true" />
              )}
              <Link to={`/s/${space.alias}`} className="side-item">
                <span className="side-glyph" aria-hidden="true">
                  <SpaceGlyph
                    alias={space.alias}
                    color={hue.base}
                    size={16}
                    family={family}
                  />
                </span>
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
                        people={members.members.map((member) => ({
                          name: member.display_name,
                          src: member.avatar,
                        }))}
                        size="sm"
                        onClick={() => openMembers(space.alias)}
                        ariaLabel={`Members of ${space.alias}`}
                      />
                    </span>
                  )
                ) : (
                  <span className="side-count">{space.engrams.length}</span>
                )}
              </Link>
            </div>

            {isOpen && (
              <>
                {space.engrams.map((engram) => (
                  <Link
                    key={engram.path}
                    to={`/s/${space.alias}/engram/${engram.path}`}
                    className={`side-page ${activeEngram === engram.path ? "active" : ""}`}
                  >
                    {engram.title || engram.path}
                  </Link>
                ))}
                <Link to={`/s/${space.alias}/new`} className="side-newpage">
                  ＋ New engram
                </Link>
              </>
            )}
          </div>
        );
      })}

      {/*
        Both of these used to wear a space's mark and sit in the space list,
        which made two actions read as two more spaces — that mark means "a
        space" in this pane, so lending it to a link that is not one is a lie.
        "New
        space" stays with the list because that is what it adds to. Inviting
        someone to reef adds a person to the product rather than to any space,
        so it goes below with the account, behind a rule.
      */}
      <Link to="/spaces/new" className="side-newspace">
        ＋ New space
      </Link>

      <div className="side-foot">
        <Link to="/invite" className="side-foot-link">
          Invite someone to <span className="reef-name">reef</span>
        </Link>
      </div>

      <AccountMenu me={me} />
    </nav>
  );
}
