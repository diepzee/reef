/**
 * A single space: its pages, and — for a shared space — a "whobar" summarizing
 * who can see it, whose avatar stack and "Manage" link open the shared
 * `MembersSheet` (owned by `AppShell`, reached via `useMembersSheet`).
 *
 * The personal space has no membership to administer, so the members
 * fetch is skipped entirely for it (`useMembers` already treats "personal"
 * as no-space) and the whobar shows a plain "only you" instead.
 *
 * The way out of a cove is at the foot of this view, and which way is on
 * offer depends on who else is in it — the two are different acts, so the
 * screen only ever shows the one that applies:
 *
 * - somebody else is here: **Leave**. If you own it, it passes to another
 *   member rather than closing. Leaving never destroys what others keep.
 * - you are alone: **Delete**, behind the cove's typed name, because there
 *   is nobody left for it to pass to and nothing of anyone else's to lose.
 */

import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, apiSend } from "../api";
import { AvatarStack } from "../components/Avatar";
import { LookPicker } from "../components/LookPicker";
import { useIndex } from "../IndexProvider";
import { relativeTime } from "../relativeTime";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";

export default function SpaceView() {
  const { space = "" } = useParams<{ space: string }>();
  const isPersonal = space === "personal";

  const { index, error: indexError, refresh } = useIndex();
  const { members, error: membersError } = useMembers(space);
  const { openMembers } = useMembersSheet();
  const navigate = useNavigate();

  const [leaving, setLeaving] = useState(false);
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [exitError, setExitError] = useState<string | null>(null);

  const thisSpace = index?.spaces.find((entry) => entry.alias === space);
  // Alone implies owner: the owner cannot remove themselves, so the last
  // person standing in a cove is always the one who owns it.
  const alone = members !== null && members.members.length === 1;
  const others = members ? members.members.length - 1 : 0;

  /**
   * Leave or destroy the cove, then send the reader somewhere that still exists.
   *
   * :param request: the call to make
   */
  async function exitCove(request: () => Promise<unknown>) {
    setLeaving(true);
    setExitError(null);
    try {
      await request();
      await refresh();
      navigate("/");
    } catch (error) {
      setExitError(
        error instanceof ApiError ? error.message : "could not complete that",
      );
      setLeaving(false);
    }
  }

  return (
    <div>
      <div className="hero">
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
          <LookPicker alias={space} />
        </>
      )}

      {!isPersonal && members && (
        <section className="delete-zone">
          <div className="export-card-copy">
            <h2>{alone ? "Delete this cove" : "Leave this cove"}</h2>
            <p>
              {alone
                ? "You are the only member. Its pages, files, and history go with it, permanently."
                : members.is_owner
                  ? `Ownership passes to another member and ${space} stays for the ` +
                    `${others === 1 ? "one other person" : `${others} other people`} in it. ` +
                    "You lose your own access; it cannot unread what you already saw."
                  : `You lose access to ${space}. It stays for everyone else, ` +
                    "and this cannot unread what you already saw."}
            </p>
          </div>

          {exitError && <div className="notice">{exitError}</div>}

          {alone ? (
            !showDelete ? (
              <button
                type="button"
                className="delete-reveal"
                onClick={() => setShowDelete(true)}
              >
                Delete {space}…
              </button>
            ) : (
              <div className="delete-guards">
                <label className="delete-phrase">
                  Type <strong>{space}</strong> to confirm
                  <input
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </label>
                <div className="delete-actions">
                  <button
                    type="button"
                    className="delete-final"
                    disabled={confirmation !== space || leaving}
                    onClick={() =>
                      exitCove(() =>
                        apiSend("DELETE", `/api/spaces/${encodeURIComponent(space)}`, {
                          confirmation: space,
                        }),
                      )
                    }
                  >
                    {leaving ? "Deleting…" : `Permanently delete ${space}`}
                  </button>
                  <button
                    type="button"
                    className="delete-cancel"
                    disabled={leaving}
                    onClick={() => {
                      setShowDelete(false);
                      setConfirmation("");
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )
          ) : confirmingLeave ? (
            <div className="delete-actions">
              <button
                type="button"
                className="delete-final"
                disabled={leaving}
                onClick={() =>
                  exitCove(() =>
                    apiSend(
                      "POST",
                      `/api/spaces/${encodeURIComponent(space)}/leave`,
                    ),
                  )
                }
              >
                {leaving ? "Leaving…" : `Confirm — leave ${space}`}
              </button>
              <button
                type="button"
                className="delete-cancel"
                disabled={leaving}
                onClick={() => setConfirmingLeave(false)}
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="delete-reveal"
              onClick={() => setConfirmingLeave(true)}
            >
              Leave {space}…
            </button>
          )}
        </section>
      )}
    </div>
  );
}
