/**
 * The shared roster/invite panel for a space — a bottom sheet under
 * `900px`, a right-side panel at or above it (see `useMediaQuery`, moved
 * out of `AppShell` so this module can share it). `AppShell` mounts the
 * single app-wide instance and hands out `openMembers(space)` via
 * `useMembersSheet`; every trigger (space header stack, "Manage", sidebar
 * stack, and — from Task 6 — the page header stack) calls that instead of
 * owning its own sheet.
 *
 * `AppShell` renders this keyed by `space` (`key={space}`), so navigating
 * from one shared space's sheet straight to another's remounts fresh: all
 * local state here (pending remove, invite fields, the disclosure
 * callout) is scoped to one space and must never survive onto the next —
 * a stale disclosure naming the wrong space/email would be a trust bug.
 * Closing (`onClose`) does not change the key — only the parent's `open`
 * flag flips — so the sheet's content stays in place while it animates
 * away instead of blanking out.
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

import { ApiError, apiSend } from "../api";
import { useIndex } from "../IndexProvider";
import { useMediaQuery } from "../useMediaQuery";
import { useMembers } from "../useMembers";
import type { InviteResult } from "../types";
import { Avatar } from "./Avatar";

/** Props for {@link MembersSheet}. */
interface MembersSheetProps {
  /** The space whose roster this sheet shows. */
  space: string;
  /** Whether the sheet is visible (drives the open/closed transform). */
  open: boolean;
  /** Called on scrim click or the × button. */
  onClose: () => void;
}

export function MembersSheet({ space, open, onClose }: MembersSheetProps) {
  const isDesktop = useMediaQuery("(min-width: 900px)");
  const { members, error, refresh } = useMembers(space);
  const { refresh: refreshIndex } = useIndex();

  const [pendingRemove, setPendingRemove] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [disclosure, setDisclosure] = useState<string | null>(null);

  // The sheet stays mounted (same `space`, same key) across a close/reopen
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

  async function confirmRemove(memberEmail: string) {
    setRemoving(true);
    setRemoveError(null);
    try {
      await apiSend("DELETE", `/api/spaces/${space}/members/${encodeURIComponent(memberEmail)}`);
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
        `/api/spaces/${space}/invites`,
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
        aria-label={`People in ${space}`}
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
            <h2 className="mbs-title">People in {space}</h2>
            <p className="mbs-sub">
              Everyone sees everything — past and future. There is no
              per-page hiding.
            </p>
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

        {members && (
          <ul className="mbs-roster">
            {members.members.map((member, index) => {
              // The API blanks every row's `email` to "" for non-owner
              // viewers (including the owner's own row) while always
              // returning the real `owner_email` — so this match can only
              // ever succeed when the caller is the owner (real emails
              // throughout). Non-owners simply don't get an OWNER tag
              // rather than risk a wrong one from name-based guessing.
              const isOwnerRow = Boolean(member.email) && member.email === members.owner_email;
              // `member.email` alone collides for every row in non-owner
              // mode (all blanked to "") — React logged "two children with
              // the same key" for it live. The list order is stable
              // (server-sorted by display name, re-fetched wholesale on
              // every change) so the index makes a safe tiebreaker.
              return (
                <li key={`${member.email}-${index}`} className="mbs-person">
                  <Avatar name={member.display_name} />
                  <div className="mbs-person-info">
                    <div className="mbs-person-name">{member.display_name}</div>
                    {member.email && (
                      <div className="mbs-person-email">{member.email}</div>
                    )}
                  </div>
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
            <form className="mbs-invite" onSubmit={handleInvite}>
              <div className="mbs-invite-title">Invite someone</div>
              {inviteError && <div className="notice">{inviteError}</div>}
              <label htmlFor="mbs-invite-email" className="sr-only">
                Email
              </label>
              <input
                id="mbs-invite-email"
                type="email"
                placeholder="email address"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="off"
                required
              />
              <label htmlFor="mbs-invite-name" className="sr-only">
                Display name (optional)
              </label>
              <input
                id="mbs-invite-name"
                placeholder="display name (optional)"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                autoComplete="off"
              />
              <button type="submit" disabled={inviting || !email}>
                {inviting ? "Sending…" : "Send invite"}
              </button>
            </form>
            {disclosure && <div className="mbs-disclose">⚠ {disclosure}</div>}
          </>
        )}
      </div>
    </>
  );
}
