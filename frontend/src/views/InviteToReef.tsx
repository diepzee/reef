/**
 * The "invite someone to reef" form: an address, and nothing shared.
 *
 * Distinct from the cove invite in `MembersSheet`, which grants permanent
 * sight of a space's contents. This one only puts an address on the
 * allowlist, so the invitee arrives in their own private personal space —
 * which is what makes it safe to send to someone merely curious.
 *
 * reef sends no email, so the success state's job is to hand the inviter
 * words to relay. A 429 (budget spent) is a routine outcome of this form,
 * not a crash, and renders inline with the date the next invite unlocks.
 */

import { useEffect, useState } from "react";

import { ApiError, apiGet, apiSend } from "../api";
import type { InviteBudget, ReefInviteResult } from "../types";

/** Colour brand mentions that arrive inside server-authored result or error text. */
function ReefText({ text }: { text: string }) {
  return text.split(/(\breef\b)/gi).map((part, index) =>
    part.toLowerCase() === "reef" ? (
      <span className="reef-name" key={index}>{part}</span>
    ) : (
      part
    ),
  );
}

export default function InviteToReef() {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [budget, setBudget] = useState<InviteBudget | null>(null);
  const [result, setResult] = useState<ReefInviteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // Showing the remaining count up front beats letting someone discover
    // the ceiling only by hitting it.
    apiGet<InviteBudget>("/api/invites")
      .then(setBudget)
      .catch(() => setBudget(null));
  }, []);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setResult(null);
    setSubmitting(true);
    try {
      const invited = await apiSend<ReefInviteResult>("POST", "/api/invites", {
        email,
        display_name: displayName || null,
      });
      setResult(invited);
      setBudget((current) =>
        current ? { ...current, invites_left: invited.invites_left } : current,
      );
      setEmail("");
      setDisplayName("");
    } catch (err) {
      if (err instanceof ApiError) {
        // The budget message already names the unlock date; a generic
        // "could not invite" here would read as a bug in the app.
        setError(err.detail ?? err.message);
      } else {
        setError("could not send the invite");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1>Invite to <span className="reef-name">reef</span></h1>
      <p className="muted">
        They get their own private <span className="reef-name">reef</span>. Nothing of yours is shared — to share a
        cove, use the members panel on that cove instead.
      </p>

      {error && <div className="notice"><ReefText text={error} /></div>}

      {result && (
        <div className="notice">
          <strong>{result.email}</strong>{" "}
          {result.already_known ? (
            <>was already on <span className="reef-name">reef</span>.</>
          ) : (
            "is now invited."
          )}
          <br />
          <ReefText text={result.next_step} />
        </div>
      )}

      <form onSubmit={handleSubmit}>
        <div className="ed-field">
          <label htmlFor="invite-email" className="ed-label">
            Email
          </label>
          <input
            id="invite-email"
            name="email"
            type="email"
            className="ed-input"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            required
          />
        </div>
        <p className="muted">
          It has to be the address they will sign in with, exactly.
        </p>

        <div className="ed-field">
          <label htmlFor="invite-name" className="ed-label">
            Name <span className="muted">(optional)</span>
          </label>
          <input
            id="invite-name"
            name="display_name"
            className="ed-input"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            autoComplete="off"
          />
        </div>

        <div className="ed-toolbar">
          <button
            type="submit"
            className="ed-save"
            disabled={submitting || !email}
          >
            {submitting ? "Inviting…" : "Invite"}
          </button>
          {budget && (
            <span className="muted">
              {budget.invites_left} of {budget.budget} left this month
            </span>
          )}
        </div>
      </form>
    </div>
  );
}
