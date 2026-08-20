/**
 * The desktop shell's persistent left pane: brand, every cove the
 * principal can see, the active cove's pages nested under it, "new page"
 * / "new cove" actions, and an account row with sign-out.
 *
 * Active state comes from the route rather than local state — `useParams`
 * only works inside a matched `<Route>`, and `Sidebar` renders alongside
 * `<Routes>` in `AppShell`, not inside one — so this parses the current
 * location's `/s/<alias>` (and `/s/<alias>/p/<path>`) prefixes instead.
 *
 * Per the brief's documented narrowing: showing every cove row's member
 * stack would be an N+1 fetch (one `useMembers` call per cove). Only the
 * *active* cove's stack is fetched and shown; every other cove row (and
 * the personal cove, which has no membership to administer) shows its
 * page count instead.
 *
 * Each cove row wears that cove's own organism in its own hue, the same
 * mark `Home`'s cards and `PageView`'s page bar carry — so a cove is
 * recognized by its creature everywhere it appears, not just on the landing
 * screen. It replaced a plain tinted dot, which carried the hue but threw
 * away the half of a cove's identity that is actually distinctive.
 */

import { useCallback, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { getCoveFolds, isCoveOpen, setCoveFold } from "../coveFolds";
import { useIndex } from "../IndexProvider";
import { useCoveLook } from "../useAppearance";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";
import type { Me } from "../types";
import { AccountMenu } from "./AccountMenu";
import { AvatarStack } from "./Avatar";
import { FrondGlyph } from "./ReefMark";
import { CoveGlyph } from "./coveGlyph";

/** Parse the active cove alias and (if on a page route) active page path from a pathname. */
function parseLocation(pathname: string): { cove: string | null; page: string | null } {
  const decoded = decodeURIComponent(pathname);
  const coveMatch = decoded.match(/^\/s\/([^/]+)/);
  const pageMatch = decoded.match(/^\/s\/[^/]+\/p\/(.*)$/);
  return {
    cove: coveMatch?.[1] ?? null,
    page: pageMatch?.[1] ?? null,
  };
}


export function Sidebar({ me }: { me: Me | null }) {
  const { index } = useIndex();
  const location = useLocation();
  const { cove: activeCove, page: activePage } = parseLocation(location.pathname);

  const { members } = useMembers(activeCove);
  const { openMembers } = useMembersSheet();
  const [folds, setFolds] = useState(getCoveFolds);
  const look = useCoveLook();

  const toggleFold = useCallback((alias: string, open: boolean) => {
    setFolds((previous) => setCoveFold(previous, alias, open));
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
      <div className="side-label">Coves</div>

      {index?.coves.map((cove) => {
        const isActive = cove.alias === activeCove;
        const isPersonal = cove.alias === "personal";
        const { hue, family } = look(cove.alias);
        const hasPages = cove.pages.length > 0;
        const isOpen = isCoveOpen(folds, cove.alias, isActive, hasPages);

        return (
          <div key={cove.alias}>
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
                  aria-label={`${isOpen ? "Collapse" : "Expand"} ${cove.alias}`}
                  onClick={() => toggleFold(cove.alias, !isOpen)}
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
              <Link to={`/s/${cove.alias}`} className="side-item">
                <span className="side-glyph" aria-hidden="true">
                  <CoveGlyph
                    alias={cove.alias}
                    color={hue.base}
                    size={16}
                    family={family}
                  />
                </span>
                <span>{cove.alias}</span>
                {isActive && !isPersonal ? (
                  members && (
                  // This sits inside the cove's own <Link>, and opening
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
                        onClick={() => openMembers(cove.alias)}
                        ariaLabel={`Members of ${cove.alias}`}
                      />
                    </span>
                  )
                ) : (
                  <span className="side-count">{cove.pages.length}</span>
                )}
              </Link>
            </div>

            {isOpen && (
              <>
                {cove.pages.map((page) => (
                  <Link
                    key={page.path}
                    to={`/s/${cove.alias}/p/${page.path}`}
                    className={`side-page ${activePage === page.path ? "active" : ""}`}
                  >
                    {page.title || page.path}
                  </Link>
                ))}
                <Link to={`/s/${cove.alias}/new`} className="side-newpage">
                  ＋ New page
                </Link>
              </>
            )}
          </div>
        );
      })}

      {/*
        Both of these used to wear a cove's mark and sit in the cove list,
        which made two actions read as two more coves — that mark means "a
        cove" in this pane, so lending it to a link that is not one is a lie.
        "New
        cove" stays with the list because that is what it adds to. Inviting
        someone to reef adds a person to the product rather than to any cove,
        so it goes below with the account, behind a rule.
      */}
      <Link to="/coves/new" className="side-newcove">
        ＋ New cove
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
