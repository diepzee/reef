/**
 * A single page, rendered from markdown to sanitized HTML.
 *
 * Pages under `meta/` (protocol, persona) are protected: they update only
 * through their own dedicated tool, never the web editor, so this view
 * shows a note instead of an Edit link for them.
 */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, apiGet } from "../api";
import { renderMarkdown } from "../markdown";
import type { Page } from "../types";

/** Path prefix reserved for protocol/persona pages; not editable here. */
const PROTECTED_PREFIX = "meta/";

export default function PageView() {
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

  const isProtected = page.path.startsWith(PROTECTED_PREFIX);

  return (
    <div>
      <h1>{page.title || page.path}</h1>
      {isProtected ? (
        <p className="disclosure">
          Protected page — the protocol and persona pages update only
          through their own dedicated tool, not this editor.
        </p>
      ) : (
        <p>
          <Link to={`/s/${space}/e/${page.path}`} className="button">
            Edit
          </Link>
        </p>
      )}
      <div
        className="page-body"
        // Safe: renderMarkdown always runs its output through DOMPurify.
        dangerouslySetInnerHTML={{ __html: renderMarkdown(page.body, space) }}
      />
    </div>
  );
}
