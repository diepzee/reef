/**
 * The engram editor: loads an existing engram, then hands off to {@link EngramEditor}.
 *
 * `EngramEditor` itself is exported for reuse: `NewEngram` renders it directly
 * in create mode, once a valid path has been chosen, so there is exactly
 * one editing UI and one save/conflict code path for both create and edit.
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, apiGet, apiSend } from "../api";
import { useIndex } from "../IndexProvider";
import { renderMarkdown } from "../markdown";
import { indexDescription } from "../summary";
import type { Engram } from "../types";

export default function Editor() {
  const { space = "", "*": path = "" } = useParams<{ space: string; "*": string }>();

  const [engram, setEngram] = useState<Engram | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setEngram(null);
    setError(null);
    apiGet<Engram>(`/api/engrams/${space}/${path}`)
      .then((loaded) => {
        if (!cancelled) setEngram(loaded);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "could not load the engram");
      });
    return () => {
      cancelled = true;
    };
  }, [space, path]);

  if (error) {
    return <div className="notice">{error}</div>;
  }
  if (engram === null) {
    return <p className="muted">Loading…</p>;
  }

  return (
    <EngramEditor
      space={space}
      path={path}
      mode="edit"
      initialTitle={engram.title}
      initialTags={engram.tags}
      initialBody={engram.body}
      initialVersion={engram.version}
    />
  );
}

/** Grow a textarea to fit its content, so the body field never scrolls internally. */
function autoGrow(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

/** Props for {@link EngramEditor}. */
interface EngramEditorProps {
  space: string;
  path: string;
  mode: "edit" | "create";
  initialTitle: string;
  initialTags: string[];
  initialBody: string;
  /** The engram's current version in edit mode; `null` for a brand-new engram. */
  initialVersion: number | null;
}

/** What the server holds once a save has raced ahead of this draft. */
interface Conflict {
  version: number;
  body: string;
}

/**
 * The editing form shared by "edit an existing engram" and "create a new engram".
 *
 * On a 409 (`version_conflict`) the draft is left exactly as the author
 * left it — no silent merge, no discarded typing — while the latest saved
 * body is fetched and shown read-only for comparison. `expectedVersion` is
 * advanced to the latest version so the next Save applies cleanly once the
 * author has manually reconciled the two.
 */
export function EngramEditor({
  space,
  path,
  mode,
  initialTitle,
  initialTags,
  initialBody,
  initialVersion,
}: EngramEditorProps) {
  const navigate = useNavigate();
  const { refresh } = useIndex();

  const [title, setTitle] = useState(initialTitle);
  const [tagsText, setTagsText] = useState(initialTags.join(", "));
  const [body, setBody] = useState(initialBody);
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState(false);
  const [expectedVersion, setExpectedVersion] = useState<number | null>(initialVersion);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);

  const bodyRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    autoGrow(bodyRef.current);
  }, [body, preview]);

  async function handleSave(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const tags = tagsText
        .split(",")
        .map((tag) => tag.trim())
        .filter((tag) => tag.length > 0);
      await apiSend<Engram>("PUT", `/api/engrams/${space}/${path}`, {
        body,
        title,
        tags,
        message,
        expected_version: expectedVersion,
      });
      await refresh();
      navigate(`/s/${space}/engram/${path}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        try {
          const latest = await apiGet<Engram>(`/api/engrams/${space}/${path}`);
          setConflict({ version: latest.version, body: latest.body });
          setExpectedVersion(latest.version);
        } catch (fetchErr: unknown) {
          setSaveError(
            fetchErr instanceof ApiError
              ? fetchErr.message
              : "someone else saved this engram, and the latest version could not be fetched",
          );
        }
      } else if (err instanceof ApiError) {
        setSaveError(err.detail ?? err.message);
      } else {
        setSaveError("could not save the engram");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h1>{mode === "create" ? "New engram" : `Edit ${path}`}</h1>

      {saveError && <div className="notice">{saveError}</div>}
      {conflict && (
        <div className="ed-conflict">
          Someone saved this engram while you were editing (now v{conflict.version}).
          Your text is kept below; the latest version is shown for comparison.
        </div>
      )}

      <form onSubmit={handleSave}>
        <div className="ed-field">
          <label htmlFor="editor-title" className="ed-label">
            Title
          </label>
          <input
            id="editor-title"
            className="ed-input"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            autoComplete="off"
          />
        </div>

        <div className="ed-field">
          <label htmlFor="editor-tags" className="ed-label">
            Tags
          </label>
          <input
            id="editor-tags"
            className="ed-input"
            value={tagsText}
            onChange={(event) => setTagsText(event.target.value)}
            placeholder="comma, separated, tags"
            autoComplete="off"
          />
        </div>

        <div className="editor-actions">
          <button
            type="button"
            className="ed-toggle"
            onClick={() => setPreview((value) => !value)}
          >
            {preview ? "Edit" : "Preview"}
          </button>
        </div>

        <div className="ed-field">
          <label htmlFor="editor-body" className="ed-label">
            Body
          </label>
          {preview ? (
            <div
              className="page-body ed-input editor-preview"
              // Safe: renderMarkdown always runs its output through DOMPurify.
              dangerouslySetInnerHTML={{ __html: renderMarkdown(body, space) }}
            />
          ) : (
            <textarea
              id="editor-body"
              ref={bodyRef}
              className="ed-input editor-textarea"
              value={body}
              onChange={(event) => setBody(event.target.value)}
              spellCheck={false}
              required
            />
          )}
        </div>

        <div className="ed-hint">
          <b>Index line:</b> {indexDescription(body) || "(none yet)"}
        </div>
        <p className="muted ed-note">
          The first prose line becomes this engram's one-line description in the index.
        </p>

        {conflict && (
          <div className="ed-field">
            <label htmlFor="editor-latest" className="ed-label">
              Latest version (read-only)
            </label>
            <textarea
              id="editor-latest"
              className="ed-input editor-textarea compare-textarea"
              value={conflict.body}
              readOnly
            />
          </div>
        )}

        <div className="ed-field">
          <label htmlFor="editor-message" className="ed-label">
            Why this change? (optional)
          </label>
          <input
            id="editor-message"
            className="ed-input"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            autoComplete="off"
          />
        </div>

        <div className="ed-toolbar">
          <button type="submit" className="ed-save" disabled={saving || !body}>
            {saving ? "Saving…" : "Save"}
          </button>
          {mode === "edit" && expectedVersion !== null && (
            <span className="ed-version-caption">
              v{expectedVersion} · saves as v{expectedVersion + 1}
            </span>
          )}
        </div>
      </form>
    </div>
  );
}
