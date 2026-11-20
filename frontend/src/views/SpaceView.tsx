/**
 * A single space: its engrams, and — for a shared space — a "whobar" summarizing
 * who can see it, whose avatar stack and "Manage" link open the shared
 * `MembersSheet` (owned by `AppShell`, reached via `useMembersSheet`).
 *
 * The hero wears the space's own organism. This is the one screen whose whole
 * subject is a single space, so it carried the weakest mark of it — a bare
 * title, no hue, while `Home`'s cards showed the creature. The controls that
 * *change* that creature live in `MembersSheet` (behind "Manage"), next to
 * "Rename for me": both are per-person settings for this space that alter
 * nothing for anybody else, and they belong together rather than one being
 * stranded in the page body between "New engram" and the delete zone.
 *
 * The personal space has no membership to administer, so the members
 * fetch is skipped entirely for it (`useMembers` already treats "personal"
 * as no-space) and the whobar shows a plain "only you" instead.
 *
 * The way out of a space is no longer here. Leaving and deleting live in
 * `MembersSheet`'s danger zone, with the rest of what one does *to* a space
 * rather than *in* it — this view is for reading what is in it, and a
 * permanent delete sitting under the engram list was one scroll away from
 * every ordinary visit.
 */

import { type CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";

import { AvatarStack } from "../components/Avatar";
import { LookPicker } from "../components/LookPicker";
import { SpaceGlyph } from "../components/spaceGlyph";
import { useIndex } from "../IndexProvider";
import { relativeTime } from "../relativeTime";
import { useSpaceLook } from "../useAppearance";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";

export default function SpaceView() {
  const { space = "" } = useParams<{ space: string }>();
  const isPersonal = space === "personal";

  const { index, error: indexError } = useIndex();
  const { members, error: membersError } = useMembers(space);
  const { openMembers } = useMembersSheet();
  const { hue, family } = useSpaceLook()(space);

  const thisSpace = index?.spaces.find((entry) => entry.alias === space);

  return (
    <div>
      <div
        className="hero"
        style={{ "--hue-base": hue.base, "--hue-light": hue.light } as CSSProperties}
      >
        <span className="hero-chip" aria-hidden="true">
          <SpaceGlyph alias={space} color={hue.base} size={34} family={family} />
        </span>
        <h1 className="hero-title">{space}</h1>
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
          <div className="section-label">Engrams</div>
          <ul className="page-rows">
            {thisSpace.engrams.length === 0 && (
              <li className="muted page-rows-empty">No engrams yet.</li>
            )}
            {thisSpace.engrams.map((engram) => (
              <li key={engram.path} className="page-row">
                <Link to={`/s/${space}/engram/${engram.path}`} className="page-row-link">
                  <span className="page-row-icon" aria-hidden="true">
                    ☰
                  </span>
                  <span className="page-row-text">
                    <span className="page-row-title">{engram.title || engram.path}</span>
                    {engram.description && (
                      <span className="page-row-desc">{engram.description}</span>
                    )}
                  </span>
                  <span className="page-row-when">
                    {relativeTime(engram.updated)}
                    {engram.last_editor && ` · ${engram.last_editor}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <Link to={`/s/${space}/new`} className="page-new">
            ＋ New engram
          </Link>
          {/*
            The personal space is the one place this stays in the page body:
            it has no roster, so no "Manage" and no sheet to move it into.
            Every shared space reaches the same control from Manage instead —
            and carries the heading and note that the sheet provides there,
            since `LookPicker` renders only the two rows.
          */}
          {isPersonal && (
            <>
              <div className="section-label">Appearance</div>
              <p className="muted look-note">
                Only you. Nobody else can see this space at all.
              </p>
              <LookPicker alias={space} />
            </>
          )}
        </>
      )}

    </div>
  );
}
