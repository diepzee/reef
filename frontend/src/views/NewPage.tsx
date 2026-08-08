/**
 * "Create a new page": a path form, then the shared editor in create mode.
 *
 * The path is chosen up front and fixed for the rest of the flow — the
 * editor that follows has no way to rename a page, only to save its body.
 */

import { useState } from "react";
import { useParams } from "react-router-dom";

import { PageEditor } from "./Editor";

/** Lowercase segments and dots/dashes/underscores, ending in `.md`. */
const PATH_PATTERN = /^[a-z0-9-/._]+\.md$/;

/** Path prefix reserved for protocol/persona pages; not creatable here. */
const PROTECTED_PREFIX = "meta/";

export default function NewPage() {
  const { space = "" } = useParams<{ space: string }>();

  const [path, setPath] = useState("");
  const [confirmedPath, setConfirmedPath] = useState<string | null>(null);
  const [pathError, setPathError] = useState<string | null>(null);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (path.startsWith(PROTECTED_PREFIX)) {
      setPathError(
        "meta/ is reserved — the protocol and persona pages update only " +
          "through their own dedicated tool, not the web editor.",
      );
      return;
    }
    if (!PATH_PATTERN.test(path)) {
      setPathError(
        'Use lowercase letters, digits, "-", "_", ".", and "/", ending in ' +
          '".md" — e.g. "trip/packing-list.md".',
      );
      return;
    }
    setPathError(null);
    setConfirmedPath(path);
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
      {pathError && <div className="notice">{pathError}</div>}
      <form onSubmit={handleSubmit}>
        <label htmlFor="new-page-path">Path</label>
        <input
          id="new-page-path"
          value={path}
          onChange={(event) => setPath(event.target.value)}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          required
        />
        <p className="muted">
          Lowercase letters, digits, "-", "_", ".", "/" — must end in ".md",
          e.g. "trip/packing-list.md". Paths starting with "meta/" are reserved.
        </p>
        <button type="submit" disabled={!path}>
          Continue
        </button>
      </form>
    </div>
  );
}
