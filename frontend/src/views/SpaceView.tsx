/**
 * A single space: its pages, and — for a shared space — a "whobar" summarizing
 * who can see it, whose avatar stack and "Manage" link open the shared
 * `MembersSheet` (owned by `AppShell`, reached via `useMembersSheet`).
 *
 * The personal space has no membership to administer, so the members
 * fetch is skipped entirely for it (`useMembers` already treats "personal"
 * as no-space) and the whobar shows a plain "only you" instead.
 */

import { Link, useParams } from "react-router-dom";

import { AvatarStack } from "../components/Avatar";
import { useIndex } from "../IndexProvider";
import { relativeTime } from "../relativeTime";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";

export default function SpaceView() {
  const { space = "" } = useParams<{ space: string }>();
  const isPersonal = space === "personal";

  const { index, error: indexError } = useIndex();
  const { members, error: membersError } = useMembers(space);
  const { openMembers } = useMembersSheet();

  const thisSpace = index?.spaces.find((entry) => entry.alias === space);

  return (
    <div>
      <div className="hero">
        <h1 className="hero-title">{isPersonal ? "Personal" : space}</h1>
      </div>

      {indexError && <div className="notice">{indexError}</div>}
      {!indexError && index === null && <p className="muted">Loading…</p>}

      {isPersonal ? (
        <div className="whobar">
          <span className="whobar-lbl">only you</span>
        </div>
      ) : (
        <div className="whobar">
          {membersError && <span className="notice">{membersError}</span>}
          {!membersError && members === null && (
            <span className="muted">Loading…</span>
          )}
          {members && (
            <>
              <AvatarStack
                names={members.members.map((member) => member.display_name)}
                onClick={() => openMembers(space)}
                ariaLabel={`Members of ${space}`}
              />
              <span className="whobar-lbl">
                {members.members.length}{" "}
                {members.members.length === 1
                  ? "member sees everything"
                  : "members see everything"}
              </span>
              {members.is_owner && (
                <button
                  type="button"
                  className="whobar-manage"
                  onClick={() => openMembers(space)}
                >
                  Manage
                </button>
              )}
            </>
          )}
        </div>
      )}

      {thisSpace && (
        <>
          <div className="section-label">Pages</div>
          <ul className="page-rows">
            {thisSpace.pages.length === 0 && (
              <li className="muted page-rows-empty">No pages yet.</li>
            )}
            {thisSpace.pages.map((page) => (
              <li key={page.path} className="page-row">
                <Link to={`/s/${space}/p/${page.path}`} className="page-row-link">
                  <span className="page-row-icon" aria-hidden="true">
                    ☰
                  </span>
                  <span className="page-row-text">
                    <span className="page-row-title">{page.title || page.path}</span>
                    {page.description && (
                      <span className="page-row-desc">{page.description}</span>
                    )}
                  </span>
                  <span className="page-row-when">
                    {relativeTime(page.updated)}
                    {page.last_editor && ` · ${page.last_editor}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <Link to={`/s/${space}/new`} className="page-new">
            ＋ New page
          </Link>
        </>
      )}
    </div>
  );
}
