/**
 * The "create a shared space" form: one slug field, one submit.
 *
 * A 400 from the backend (invalid or already-taken slug) is a routine,
 * expected outcome of this form, not a crash — it renders inline via
 * `ApiError.detail` rather than escaping to an error boundary.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, apiSend } from "../api";

export default function NewSpace() {
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await apiSend<{ alias: string; slug: string }>("POST", "/api/spaces", {
        slug,
      });
      navigate(`/s/${slug}`);
    } catch (err) {
      setSubmitting(false);
      if (err instanceof ApiError) {
        setError(err.detail ?? err.message);
      } else {
        setError("could not create the space");
      }
    }
  }

  return (
    <div>
      <h1>New space</h1>
      {error && <div className="notice">{error}</div>}
      <form onSubmit={handleSubmit}>
        <label htmlFor="slug">Name</label>
        <input
          id="slug"
          name="slug"
          value={slug}
          onChange={(event) => setSlug(event.target.value)}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          required
        />
        <p className="muted">Lowercase letters, digits, and hyphens — e.g. "trip".</p>
        <button type="submit" disabled={submitting || !slug}>
          {submitting ? "Creating…" : "Create space"}
        </button>
      </form>
    </div>
  );
}
