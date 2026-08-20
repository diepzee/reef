/**
 * The "create a shared cove" form: one slug field, one submit.
 *
 * A 400 from the backend (invalid or already-taken slug) is a routine,
 * expected outcome of this form, not a crash — it renders inline via
 * `ApiError.detail` rather than escaping to an error boundary.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, apiSend } from "../api";
import { useIndex } from "../IndexProvider";

export default function NewCove() {
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();
  const { refresh } = useIndex();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await apiSend<{ alias: string; slug: string }>("POST", "/api/coves", {
        slug,
      });
      await refresh();
      navigate(`/s/${slug}`);
    } catch (err) {
      setSubmitting(false);
      if (err instanceof ApiError) {
        setError(err.detail ?? err.message);
      } else {
        setError("could not create the cove");
      }
    }
  }

  return (
    <div>
      <h1>New cove</h1>
      {error && <div className="notice">{error}</div>}
      <form onSubmit={handleSubmit}>
        <div className="ed-field">
          <label htmlFor="slug" className="ed-label">
            Name
          </label>
          <input
            id="slug"
            name="slug"
            className="ed-input"
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            required
          />
        </div>
        <p className="muted">Lowercase letters, digits, and hyphens — e.g. "trip".</p>
        <div className="ed-toolbar">
          <button type="submit" className="ed-save" disabled={submitting || !slug}>
            {submitting ? "Creating…" : "Create cove"}
          </button>
        </div>
      </form>
    </div>
  );
}
