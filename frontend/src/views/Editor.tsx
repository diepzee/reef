/**
 * The page editor: loads an existing page, then hands off to {@link PageEditor}.
 *
 * `PageEditor` itself is exported for reuse: `NewPage` renders it directly
 * in create mode, once a valid path has been chosen, so there is exactly
 * one editing UI and one save/conflict code path for both create and edit.
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, apiGet, apiSend } from "../api";
import { useIndex } from "../IndexProvider";
import { renderMarkdown } from "../markdown";
import { indexDescription } from "../summary";
import type { Page } from "../types";

export default function Editor() {
  const { space = "", "*": path = "" } = useParams<{ space: string; "*": string }>();

  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPage(null);
    setError(null);
    apiGet<Page>(`/api/pages/${space}/${path}`)
      .then((loaded) => {
        if (!cancelled) setPage(loaded);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "could not load the page");
      });
    return () => {
      cancelled = true;
    };
  }, [space, path]);

  if (error) {
    return <div className="notice">{error}</div>;
  }
  if (page === null) {
    return <p className="muted">Loading…</p>;
  }

  return (
    <PageEditor
      space={space}
      path={path}
      mode="edit"
      initialTitle={page.title}
      initialTags={page.tags}
      initialBody={page.body}
      initialVersion={page.version}
    />
  );
}

/** Grow a textarea to fit its content, so the body field never scrolls internally. */
function autoGrow(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

/** Props for {@link PageEditor}. */
interface PageEditorProps {
  space: string;
  path: string;
  mode: "edit" | "create";
  initialTitle: string;
  initialTags: string[];
  initialBody: string;
  /** The page's current version in edit mode; `null` for a brand-new page. */
  initialVersion: number | null;
}

/** What the server holds once a save has raced ahead of this draft. */
interface Conflict {
  version: number;
  body: string;
}

/**
 * The editing form shared by "edit an existing page" and "create a new page".
 *
 * On a 409 (`version_conflict`) the draft is left exactly as the author
 * left it — no silent merge, no discarded typing — while the latest saved
 * body is fetched and shown read-only for comparison. `expectedVersion` is
 * advanced to the latest version so the next Save applies cleanly once the
 * author has manually reconciled the two.
 */
export function PageEditor({
  space,
  path,
  mode,
  initialTitle,
  initialTags,
  initialBody,
  initialVersion,
}: PageEditorProps) {
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
      await apiSend<Page>("PUT", `/api/pages/${space}/${path}`, {
        body,
        title,
        tags,
        message,
        expected_version: expectedVersion,
      });
      await refresh();
      navigate(`/s/${space}/p/${path}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        try {
          const latest = await apiGet<Page>(`/api/pages/${space}/${path}`);
          setConflict({ version: latest.version, body: latest.body });
          setExpectedVersion(latest.version);
        } catch (fetchErr: unknown) {
          setSaveError(
            fetchErr instanceof ApiError
              ? fetchErr.message
              : "someone else saved this page, and the latest version could not be fetched",
          );
        }
      } else if (err instanceof ApiError) {
        setSaveError(err.detail ?? err.message);
      } else {
        setSaveError("could not save the page");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h1>{mode === "create" ? "New page" : `Edit ${path}`}</h1>

      {saveError && <div className="notice">{saveError}</div>}
      {conflict && (
        <div className="warning">
          Someone saved this page while you were editing (now v{conflict.version}).
          Your text is kept below; the latest version is shown for comparison.
        </div>
      )}

      <form onSubmit={handleSave}>
        <label htmlFor="editor-title">Title</label>
        <input
          id="editor-title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          autoComplete="off"
        />

        <label htmlFor="editor-tags">Tags</label>
        <input
          id="editor-tags"
          value={tagsText}
          onChange={(event) => setTagsText(event.target.value)}
          placeholder="comma, separated, tags"
          autoComplete="off"
        />

        <div className="editor-actions">
          <button type="button" onClick={() => setPreview((value) => !value)}>
            {preview ? "Edit" : "Preview"}
          </button>
        </div>

        <label htmlFor="editor-body">Body</label>
        {preview ? (
          <div
            className="page-body editor-preview"
            // Safe: renderMarkdown always runs its output through DOMPurify.
            dangerouslySetInnerHTML={{ __html: renderMarkdown(body, space) }}
          />
        ) : (
          <textarea
            id="editor-body"
            ref={bodyRef}
            className="editor-textarea"
            value={body}
            onChange={(event) => setBody(event.target.value)}
            spellCheck={false}
            required
          />
        )}

        <div className="index-description">
          <p>
            <strong>Index description:</strong> {indexDescription(body) || "(none yet)"}
          </p>
          <p className="muted">
            The first prose line becomes this page's one-line description in the index.
          </p>
        </div>

        {conflict && (
          <>
            <label htmlFor="editor-latest">Latest version (read-only)</label>
            <textarea
              id="editor-latest"
              className="editor-textarea compare-textarea"
              value={conflict.body}
              readOnly
            />
          </>
        )}

        <label htmlFor="editor-message">Why this change? (optional)</label>
        <input
          id="editor-message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          autoComplete="off"
        />

        <div className="editor-actions">
          <button type="submit" disabled={saving || !body}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}
