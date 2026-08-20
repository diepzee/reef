/**
 * A single cove: its pages, and — for a shared cove — a "whobar" summarizing
 * who can see it, whose avatar stack and "Manage" link open the shared
 * `MembersSheet` (owned by `AppShell`, reached via `useMembersSheet`).
 *
 * The hero wears the cove's own organism. This is the one screen whose whole
 * subject is a single cove, so it carried the weakest mark of it — a bare
 * title, no hue, while `Home`'s cards showed the creature. The controls that
 * *change* that creature live in `MembersSheet` (behind "Manage"), next to
 * "Rename for me": both are per-person settings for this cove that alter
 * nothing for anybody else, and they belong together rather than one being
 * stranded in the page body between "New page" and the delete zone.
 *
 * The personal cove has no membership to administer, so the members
 * fetch is skipped entirely for it (`useMembers` already treats "personal"
 * as no-cove) and the whobar shows a plain "only you" instead.
 *
 * The way out of a cove is no longer here. Leaving and deleting live in
 * `MembersSheet`'s danger zone, with the rest of what one does *to* a cove
 * rather than *in* it — this view is for reading what is in it, and a
 * permanent delete sitting under the page list was one scroll away from
 * every ordinary visit.
 */

import { type CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";

import { AvatarStack } from "../components/Avatar";
import { LookPicker } from "../components/LookPicker";
import { CoveGlyph } from "../components/coveGlyph";
import { useIndex } from "../IndexProvider";
import { relativeTime } from "../relativeTime";
import { useCoveLook } from "../useAppearance";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";

export default function CoveView() {
  const { cove = "" } = useParams<{ cove: string }>();
  const isPersonal = cove === "personal";

  const { index, error: indexError } = useIndex();
  const { members, error: membersError } = useMembers(cove);
  const { openMembers } = useMembersSheet();
  const { hue, family } = useCoveLook()(cove);

  const thisCove = index?.coves.find((entry) => entry.alias === cove);

  return (
    <div>
      <div
        className="hero"
        style={{ "--hue-base": hue.base, "--hue-light": hue.light } as CSSProperties}
      >
        <span className="hero-chip" aria-hidden="true">
          <CoveGlyph alias={cove} color={hue.base} size={26} family={family} />
        </span>
        <h1 className="hero-title">{cove}</h1>
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
                people={members.members.map((member) => ({
                  name: member.display_name,
                  src: member.avatar,
                }))}
                onClick={() => openMembers(cove)}
                ariaLabel={`Members of ${cove}`}
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
                  onClick={() => openMembers(cove)}
                >
                  Manage
                </button>
              )}
            </>
          )}
        </div>
      )}

      {thisCove && (
        <>
          <div className="section-label">Pages</div>
          <ul className="page-rows">
            {thisCove.pages.length === 0 && (
              <li className="muted page-rows-empty">No pages yet.</li>
            )}
            {thisCove.pages.map((page) => (
              <li key={page.path} className="page-row">
                <Link to={`/s/${cove}/p/${page.path}`} className="page-row-link">
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
          <Link to={`/s/${cove}/new`} className="page-new">
            ＋ New page
          </Link>
          {/*
            The personal cove is the one place this stays in the page body:
            it has no roster, so no "Manage" and no sheet to move it into.
            Every shared cove reaches the same control from Manage instead —
            and carries the heading and note that the sheet provides there,
            since `LookPicker` renders only the two rows.
          */}
          {isPersonal && (
            <>
              <div className="section-label">Appearance</div>
              <p className="muted look-note">
                Only you. Nobody else can see this cove at all.
              </p>
              <LookPicker alias={cove} />
            </>
          )}
        </>
      )}

    </div>
  );
}
