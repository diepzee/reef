/**
 * A single space: its pages, and — for the owner of a shared space — the
 * member roster and invite form.
 *
 * The personal space has no membership to administer, so the members
 * fetch is skipped entirely for it rather than firing a request the
 * backend has nothing to answer.
 */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, apiGet, apiSend } from "../api";
import { relativeTime } from "../relativeTime";
import type { IndexPayload, InviteResult, Members } from "../types";

export default function SpaceView() {
  const { space = "" } = useParams<{ space: string }>();
  const isPersonal = space === "personal";

  const [index, setIndex] = useState<IndexPayload | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);
  const [members, setMembers] = useState<Members | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIndex(null);
    setIndexError(null);
    setMembers(null);
    setMembersError(null);

    apiGet<IndexPayload>("/api/index")
      .then((payload) => {
        if (!cancelled) setIndex(payload);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setIndexError(err instanceof ApiError ? err.message : "could not load the space");
      });

    if (!isPersonal) {
      apiGet<Members>(`/api/spaces/${space}/members`)
        .then((payload) => {
          if (!cancelled) setMembers(payload);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setMembersError(
            err instanceof ApiError ? err.message : "could not load members",
          );
        });
    }

    return () => {
      cancelled = true;
    };
  }, [space, isPersonal]);

  const thisSpace = index?.spaces.find((entry) => entry.alias === space);

  /** Re-fetch the member roster after an invite or removal changes it. */
  function reloadMembers() {
    apiGet<Members>(`/api/spaces/${space}/members`)
      .then(setMembers)
      .catch((err: unknown) => {
        setMembersError(err instanceof ApiError ? err.message : "could not load members");
      });
  }

  return (
    <div>
      <h1>{space === "personal" ? "Personal" : space}</h1>

      {indexError && <div className="notice">{indexError}</div>}
      {!indexError && index === null && <p className="muted">Loading…</p>}

      {thisSpace && (
        <>
          <ul className="page-list">
            {thisSpace.pages.length === 0 && (
              <li className="muted">No pages yet.</li>
            )}
            {thisSpace.pages.map((page) => (
              <li key={page.path} className="page-item">
                <Link to={`/s/${space}/p/${page.path}`} className="page-item-title">
                  {page.title || page.path}
                </Link>
                {page.description && (
                  <p className="page-item-description">{page.description}</p>
                )}
                <p className="muted page-item-meta">
                  updated {relativeTime(page.updated)}
                </p>
              </li>
            ))}
          </ul>
          <p>
            <Link to={`/s/${space}/new`} className="button">
              New page
            </Link>
          </p>
        </>
      )}

      {!isPersonal && (
        <MembersPanel
          space={space}
          members={members}
          error={membersError}
          onChanged={reloadMembers}
        />
      )}
    </div>
  );
}

/** Props for {@link MembersPanel}. */
interface MembersPanelProps {
  space: string;
  members: Members | null;
  error: string | null;
  onChanged: () => void;
}

/**
 * The member roster and invite form — owner-only.
 *
 * The members fetch itself always runs for a shared space (it is how
 * `is_owner` gets known in the first place), but the panel renders
 * nothing once loaded unless the caller owns the space.
 */
function MembersPanel({ space, members, error, onChanged }: MembersPanelProps) {
  const [pendingRemove, setPendingRemove] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);

  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [disclosure, setDisclosure] = useState<string | null>(null);

  async function confirmRemove(memberEmail: string) {
    setRemoving(true);
    setRemoveError(null);
    try {
      await apiSend("DELETE", `/api/spaces/${space}/members/${encodeURIComponent(memberEmail)}`);
      setPendingRemove(null);
      onChanged();
    } catch (err) {
      setRemoveError(err instanceof ApiError ? err.message : "could not remove member");
    } finally {
      setRemoving(false);
    }
  }

  async function handleInvite(event: React.FormEvent) {
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
      onChanged();
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

  if (error) {
    return <div className="notice">{error}</div>;
  }
  if (members === null) {
    return <p className="muted">Loading members…</p>;
  }
  if (!members.is_owner) {
    return null;
  }

  return (
    <section className="members-panel">
      <h2>Members</h2>
      {removeError && <div className="notice">{removeError}</div>}
      <ul className="member-list">
        {members.members.map((member) => {
          const isOwnerRow = member.email === members.owner_email;
          return (
            <li key={member.email} className="member-row">
              <div>
                <div>{member.display_name}</div>
                <div className="muted">{member.email}</div>
              </div>
              {!isOwnerRow && (
                <div className="member-actions">
                  {pendingRemove === member.email ? (
                    <>
                      <button
                        type="button"
                        className="danger"
                        disabled={removing}
                        onClick={() => confirmRemove(member.email)}
                      >
                        Confirm remove
                      </button>
                      <button
                        type="button"
                        disabled={removing}
                        onClick={() => setPendingRemove(null)}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button type="button" onClick={() => setPendingRemove(member.email)}>
                      Remove
                    </button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <h3>Invite someone</h3>
      {inviteError && <div className="notice">{inviteError}</div>}
      {disclosure && <div className="disclosure">{disclosure}</div>}
      <form onSubmit={handleInvite}>
        <label htmlFor="invite-email">Email</label>
        <input
          id="invite-email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="off"
          required
        />
        <label htmlFor="invite-name">Display name (optional)</label>
        <input
          id="invite-name"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
          autoComplete="off"
        />
        <button type="submit" disabled={inviting || !email}>
          {inviting ? "Inviting…" : "Invite"}
        </button>
      </form>
    </section>
  );
}
