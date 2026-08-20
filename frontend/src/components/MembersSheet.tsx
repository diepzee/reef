/**
 * The shared roster/invite panel for a cove — a bottom sheet under
 * `900px`, a right-side panel at or above it (see `useMediaQuery`, moved
 * out of `AppShell` so this module can share it). `AppShell` mounts the
 * single app-wide instance and hands out `openMembers(cove)` via
 * `useMembersSheet`; every trigger (cove header stack, "Manage", sidebar
 * stack, and — from Task 6 — the page header stack) calls that instead of
 * owning its own sheet.
 *
 * `AppShell` renders this keyed by `cove` (`key={cove}`), so navigating
 * from one shared cove's sheet straight to another's remounts fresh: all
 * local state here (pending remove, invite fields, the disclosure
 * callout) is scoped to one cove and must never survive onto the next —
 * a stale disclosure naming the wrong cove/email would be a trust bug.
 * Closing (`onClose`) does not change the key — only the parent's `open`
 * flag flips — so the sheet's content stays in place while it animates
 * away instead of blanking out.
 *
 * This is also where a cove's per-person settings live — "Rename for me" in
 * the head and `LookPicker` at the foot. Both change this cove for the
 * viewer alone and for nobody else in it, which is the property that groups
 * them; the look picker used to sit loose in `CoveView`'s body, between
 * "New page" and the delete zone, where it read as a property of the cove
 * itself.
 *
 * Two modes come from `members.is_owner`: owners get the invite form and
 * two-step "Remove…" per non-owner row; non-owners see the roster only.
 * For a non-owner viewer the backend blanks every row's `member.email` to
 * `""` — including the owner's own row — while `members.owner_email`
 * always stays real, so the email line is hidden whenever it's empty
 * rather than rendering a blank line, and the OWNER tag (matched by
 * `member.email === members.owner_email`) can only ever resolve for an
 * owner viewer; non-owners simply don't see it rather than risk tagging
 * the wrong row.
 *
 * Every successful invite/remove calls `refresh()` (from `useMembers`,
 * whose module-scope cache is shared with every other mounted consumer —
 * notably the sidebar's avatar stack) so the change is visible everywhere
 * without a remount, plus `useIndex().refresh()` since the index is the
 * other piece of shared state a membership change could affect.
 */

import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, apiSend } from "../api";
import { useIndex } from "../IndexProvider";
import { useCoveLook } from "../useAppearance";
import { useMe } from "../useMe";
import { useMediaQuery } from "../useMediaQuery";
import { useMembers } from "../useMembers";
import type { InviteResult } from "../types";
import { Avatar } from "./Avatar";
import { LookPicker } from "./LookPicker";
import { CoveGlyph } from "./coveGlyph";

/** Props for {@link MembersSheet}. */
interface MembersSheetProps {
  /** The cove whose roster this sheet shows. */
  cove: string;
  /** Whether the sheet is visible (drives the open/closed transform). */
  open: boolean;
  /** Called on scrim click or the × button. */
  onClose: () => void;
}

export function MembersSheet({ cove, open, onClose }: MembersSheetProps) {
  const navigate = useNavigate();
  const isDesktop = useMediaQuery("(min-width: 900px)");
  const { members, error, refresh } = useMembers(cove);
  const { hue, family } = useCoveLook()(cove);
  const { me } = useMe();
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const { refresh: refreshIndex } = useIndex();

  // The way out of the cove, moved here from CoveView's body.
  const [leaving, setLeaving] = useState(false);
  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [exitError, setExitError] = useState<string | null>(null);

  // Alone implies owner: the owner cannot remove themselves, so the last
  // person standing in a cove is always the one who owns it.
  const alone = members !== null && members.members.length === 1;
  const others = members ? members.members.length - 1 : 0;

  const [pendingRemove, setPendingRemove] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [disclosure, setDisclosure] = useState<string | null>(null);

  // The sheet stays mounted (same `cove`, same key) across a close/reopen
  // cycle so the close transition has something to animate — but that
  // means, unlike a remount, nothing else clears these on its own. Without
  // this, closing after an invite and reopening later would re-show that
  // invite's disclosure (or a stale remove-confirmation state) as if it
  // just happened.
  useEffect(() => {
    if (!open) {
      setPendingRemove(null);
      setRemoveError(null);
      setInviteError(null);
      setDisclosure(null);
      // The exit guards reset too: reopening the sheet must not find a
      // half-armed "Permanently delete" from a visit the reader abandoned.
      setConfirmingLeave(false);
      setShowDelete(false);
      setConfirmation("");
      setExitError(null);
    }
  }, [open]);

  // Escape closes the sheet — the natural keyboard pairing for the ×
  // button and scrim click, and only wired while open so it doesn't
  // shadow Escape elsewhere in the app.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  /**
   * Rename this cove for the signed-in person only.
   *
   * The name lives on their own membership, so nobody else's changes — but
   * it is also what the URL addresses, so the old path stops resolving the
   * moment this succeeds and the view has to follow it.
   */
  async function rename(event: FormEvent) {
    event.preventDefault();
    const wanted = newName.trim();
    if (!wanted || wanted === cove) {
      setRenaming(false);
      return;
    }
    setRenameError(null);
    try {
      await apiSend<{ was: string; now: string }>(
        "POST",
        `/api/coves/${encodeURIComponent(cove)}/name`,
        { name: wanted },
      );
      await refreshIndex();
      setRenaming(false);
      onClose();
      navigate(`/s/${encodeURIComponent(wanted)}`, { replace: true });
    } catch (problem) {
      setRenameError(
        problem instanceof ApiError ? problem.detail ?? problem.message : "could not rename",
      );
    }
  }

  /**
   * Leave or destroy the cove, then send the reader somewhere that still
   * exists — the sheet is closed first, since it belongs to a cove that is
   * about to stop being reachable.
   *
   * :param request: the call to make
   */
  async function exitCove(request: () => Promise<unknown>) {
    setLeaving(true);
    setExitError(null);
    try {
      await request();
      await refreshIndex();
      onClose();
      navigate("/");
    } catch (problem) {
      setExitError(
        problem instanceof ApiError ? problem.message : "could not complete that",
      );
      setLeaving(false);
    }
  }

  async function confirmRemove(memberEmail: string) {
    setRemoving(true);
    setRemoveError(null);
    try {
      await apiSend("DELETE", `/api/coves/${cove}/members/${encodeURIComponent(memberEmail)}`);
      setPendingRemove(null);
      await refresh();
      refreshIndex();
    } catch (err) {
      setRemoveError(err instanceof ApiError ? err.message : "could not remove member");
    } finally {
      setRemoving(false);
    }
  }

  async function handleInvite(event: FormEvent) {
    event.preventDefault();
    setInviting(true);
    setInviteError(null);
    try {
      const result = await apiSend<InviteResult>(
        "POST",
        `/api/coves/${cove}/invites`,
        { email, display_name: displayName || null },
      );
      setDisclosure(result.disclosure);
      setEmail("");
      setDisplayName("");
      await refresh();
      refreshIndex();
    } catch (err) {
      if (err instanceof ApiError) {
        setInviteError(err.detail ?? err.message);
      } else {
        setInviteError("could not send the invite");
      }
    } finally {
      setInviting(false);
    }
  }

  return (
    <>
      <div
        className={`mbs-scrim ${open ? "mbs-scrim-open" : ""}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        className={`mbs-sheet ${open ? "mbs-sheet-open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={`People in ${cove}`}
        // `inert` (not `aria-hidden`) while closed: `aria-hidden` on a
        // container is purely an AT hint — it does not stop a descendant
        // (e.g. the × button, right after a click) from *keeping* DOM
        // focus, and Chrome flags that combination as a violation
        // ("Blocked aria-hidden on an element because its descendant
        // retained focus"). `inert` actually removes the subtree from
        // both focus and the accessibility tree, so the browser moves
        // focus off the close button itself when the sheet closes.
        inert={!open}
      >
        {!isDesktop && <div className="mbs-grip" aria-hidden="true" />}
        <div className="mbs-head">
          <div>
            <h2 className="mbs-title">
              <span className="mbs-glyph" aria-hidden="true">
                <CoveGlyph alias={cove} color={hue.base} size={20} family={family} />
              </span>
              People in {cove}
            </h2>
            <p className="mbs-sub">Everyone here can see every page.</p>
          </div>
          <button
            type="button"
            className="mbs-close"
            onClick={onClose}
            aria-label="Close members sheet"
          >
            ×
          </button>
        </div>

        {error && <div className="notice">{error}</div>}
        {removeError && <div className="notice">{removeError}</div>}
        {members === null && !error && <p className="muted">Loading members…</p>}

        {members && <div className="mbs-label">Members</div>}

        {members && (
          <ul className="mbs-roster">
            {members.members.map((member) => {
              // The API blanks every row's `email` to "" for non-owner
              // viewers (including the owner's own row) while always
              // returning the real `owner_email` — so this match can only
              // ever succeed when the caller is the owner (real emails
              // throughout). Non-owners simply don't get an OWNER tag
              // rather than risk a wrong one from name-based guessing.
              const isOwnerRow = Boolean(member.email) && member.email === members.owner_email;
              // Which row is the reader's own. `person_id` answers it for
              // every viewer; `email` could not, being blanked for non-owners
              // — the very people for whom the rest of the roster is most
              // anonymous, and who therefore benefit most from the marker.
              const isYou = me !== null && member.person_id === me.person_id;
              // `person_id` is the real key. `member.email` alone collided
              // for every row in non-owner mode (all blanked to "") — React
              // logged "two children with the same key" for it live — and
              // the `${email}-${index}` tiebreaker that replaced it was only
              // ever stable because the server sorts the roster.
              return (
                <li key={member.person_id} className="mbs-person">
                  <Avatar name={member.display_name} src={member.avatar} size="lg" />
                  <div className="mbs-person-info">
                    <div className="mbs-person-name">{member.display_name}</div>
                    {member.email && (
                      <div className="mbs-person-email">{member.email}</div>
                    )}
                  </div>
                  {isYou && <span className="mbs-you-tag">You</span>}
                  {isOwnerRow && <span className="mbs-owner-tag">Owner</span>}
                  {members.is_owner && !isOwnerRow && (
                    <span className="mbs-person-actions">
                      {pendingRemove === member.email ? (
                        <>
                          <button
                            type="button"
                            className="mbs-confirm-remove"
                            disabled={removing}
                            onClick={() => confirmRemove(member.email)}
                          >
                            Confirm remove
                          </button>
                          <button
                            type="button"
                            className="mbs-cancel-remove"
                            disabled={removing}
                            onClick={() => setPendingRemove(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="mbs-remove"
                          onClick={() => setPendingRemove(member.email)}
                        >
                          Remove…
                        </button>
                      )}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {members?.is_owner && (
          <>
            <div className="mbs-label">Invite a person</div>
            <form className="mbs-invite" onSubmit={handleInvite}>
              {inviteError && <div className="notice">{inviteError}</div>}
              <div className="mbs-invite-row">
                <label htmlFor="mbs-invite-email" className="sr-only">
                  Email address
                </label>
                <input
                  id="mbs-invite-email"
                  type="email"
                  placeholder="Email address"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="off"
                  required
                />
                <button type="submit" disabled={inviting || !email}>
                  {inviting ? "Sending…" : "Invite"}
                </button>
              </div>
              {/* Secondary, and below: an address is all an invite needs,
                  and this only spares the invitee an ugly auto-name. */}
              <label htmlFor="mbs-invite-name" className="sr-only">
                Display name (optional)
              </label>
              <input
                id="mbs-invite-name"
                className="mbs-invite-name"
                placeholder="Their name (optional)"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                autoComplete="off"
              />
            </form>
            {disclosure && <div className="mbs-disclose">⚠ {disclosure}</div>}
          </>
        )}

        {/*
          For everybody, not just owners: how a cove reads to you is the one
          thing in this sheet a non-owner can change. All three settings here
          — the name, the colour, the creature — change this cove for you and
          for nobody else in it, which is why they share a section and why
          the note below says it once rather than three times.
        */}
        <div className="mbs-label">Appearance</div>
        <p className="mbs-note">
          Only you. Everyone else keeps seeing this cove their own way.
        </p>
        {renameError && <div className="notice">{renameError}</div>}
        <div className="look-field">
          <div className="look-label">Name</div>
          {renaming ? (
            <form className="mbs-rename" onSubmit={rename}>
              <input
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                aria-label="New cove name"
              />
              <button type="submit">Save</button>
              <button
                type="button"
                onClick={() => {
                  setRenaming(false);
                  setRenameError(null);
                }}
              >
                Cancel
              </button>
            </form>
          ) : (
            <button
              type="button"
              className="mbs-rename-open"
              onClick={() => {
                setNewName(cove);
                setRenaming(true);
              }}
            >
              {cove}
              <span className="mbs-rename-hint">Rename for me</span>
            </button>
          )}
        </div>
        <LookPicker alias={cove} />

        {/*
          The way out, moved here from CoveView's body. Which way is on
          offer depends on who else is in the cove — they are different acts,
          so only the one that applies is ever shown:

          - somebody else is here: Leave. If you own it, it passes to another
            member rather than closing. Leaving never destroys what others keep.
          - you are alone: Delete, behind the cove's typed name, because there
            is nobody left for it to pass to and nothing of anyone else's to lose.
        */}
        {members && (
          <>
            <div className="mbs-label mbs-label-danger">Danger zone</div>
            <p className="mbs-note">
              {alone
                ? "You are the only member. Its pages, files, and history go with it, permanently."
                : members.is_owner
                  ? `Ownership passes to another member and ${cove} stays for the ` +
                    `${others === 1 ? "one other person" : `${others} other people`} in it. ` +
                    "You lose your own access; it cannot unread what you already saw."
                  : `You lose access to ${cove}. It stays for everyone else, ` +
                    "and this cannot unread what you already saw."}
            </p>
            {exitError && <div className="notice">{exitError}</div>}

            {alone ? (
              !showDelete ? (
                <button
                  type="button"
                  className="mbs-danger"
                  onClick={() => setShowDelete(true)}
                >
                  Delete this cove…
                </button>
              ) : (
                <div className="mbs-danger-guards">
                  <label className="mbs-danger-phrase">
                    Type <strong>{cove}</strong> to confirm
                    <input
                      value={confirmation}
                      onChange={(event) => setConfirmation(event.target.value)}
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </label>
                  <div className="mbs-danger-actions">
                    <button
                      type="button"
                      className="mbs-danger mbs-danger-final"
                      disabled={confirmation !== cove || leaving}
                      onClick={() =>
                        exitCove(() =>
                          apiSend(
                            "DELETE",
                            `/api/coves/${encodeURIComponent(cove)}`,
                            { confirmation: cove },
                          ),
                        )
                      }
                    >
                      {leaving ? "Deleting…" : `Permanently delete ${cove}`}
                    </button>
                    <button
                      type="button"
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
              <div className="mbs-danger-actions">
                <button
                  type="button"
                  className="mbs-danger mbs-danger-final"
                  disabled={leaving}
                  onClick={() =>
                    exitCove(() =>
                      apiSend(
                        "POST",
                        `/api/coves/${encodeURIComponent(cove)}/leave`,
                      ),
                    )
                  }
                >
                  {leaving ? "Leaving…" : `Confirm — leave ${cove}`}
                </button>
                <button
                  type="button"
                  disabled={leaving}
                  onClick={() => setConfirmingLeave(false)}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="mbs-danger"
                onClick={() => setConfirmingLeave(true)}
              >
                Leave this cove
              </button>
            )}
          </>
        )}
      </div>
    </>
  );
}
