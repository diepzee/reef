/**
 * "Create a new page": a path form, then the shared editor in create mode.
 *
 * The path is chosen up front and fixed for the rest of the flow — the
 * editor that follows has no way to rename a page, only to save its body.
 */

import { useState } from "react";
import { useParams } from "react-router-dom";

import { normalizePagePath, pagePathProblem } from "../pagePath";
import { PageEditor } from "./Editor";

export default function NewPage() {
  const { space = "" } = useParams<{ space: string }>();

  const [path, setPath] = useState("");
  const [confirmedPath, setConfirmedPath] = useState<string | null>(null);

  // Both derived from what is in the box right now, so the form answers
  // while they type rather than waiting for a rejected submit.
  const normalized = normalizePagePath(path);
  const problem = pagePathProblem(normalized);
  const fixed = normalized !== "" && normalized !== path.trim();

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!normalized || problem) return;
    setConfirmedPath(normalized);
  }

  if (confirmedPath) {
    return (
      <PageEditor
        space={space}
        path={confirmedPath}
        mode="create"
        initialTitle=""
        initialTags={[]}
        initialBody=""
        initialVersion={null}
      />
    );
  }

  return (
    <div>
      <h1>New page</h1>
      <form onSubmit={handleSubmit}>
        <div className="ed-field">
          <label htmlFor="new-page-path" className="ed-label">
            Path
          </label>
          <input
            id="new-page-path"
            className="ed-input"
            value={path}
            onChange={(event) => setPath(event.target.value)}
            aria-invalid={problem ? true : undefined}
            aria-describedby="new-page-path-hint"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            required
          />
        </div>
        <p className="muted" id="new-page-path-hint" aria-live="polite">
          {problem ? (
            <span className="ed-problem">{problem}</span>
          ) : fixed ? (
            <>
              Will be created as <code>{normalized}</code>.
            </>
          ) : (
            <>
              Something like <code>trip/packing-list</code> — “.md” is added
              for you, and “meta/” is reserved.
            </>
          )}
        </p>
        <div className="ed-toolbar">
          <button
            type="submit"
            className="ed-save"
            disabled={!normalized || problem !== null}
          >
            Continue
          </button>
        </div>
      </form>
    </div>
  );
}
