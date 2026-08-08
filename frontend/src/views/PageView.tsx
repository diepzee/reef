/**
 * A single page, rendered from markdown to sanitized HTML.
 *
 * Pages under `meta/` (protocol, persona) are protected: they update only
 * through their own dedicated tool, never the web editor, so this view
 * shows a note instead of an Edit link for them.
 *
 * The page bar's avatar stack reuses the same `useMembers` +
 * `useMembersSheet` mechanism as `SpaceView`'s whobar and `Sidebar`'s
 * active-space stack, so clicking it opens the one shared `MembersSheet`
 * (owned by `AppShell`) for this page's space. The personal space has no
 * membership to administer — `useMembers` already returns `null` for it —
 * so the stack simply doesn't render there, per the brief's "the user's
 * own avatar or none" allowance.
 */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, apiGet } from "../api";
import { AvatarStack } from "../components/Avatar";
import { pageMetaSentence } from "../components/pageMeta";
import { renderMarkdown } from "../markdown";
import type { Page } from "../types";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";

/** Path prefix reserved for protocol/persona pages; not editable here. */
const PROTECTED_PREFIX = "meta/";

export default function PageView() {
  const { space = "", "*": path = "" } = useParams<{ space: string; "*": string }>();
  const isPersonal = space === "personal";

  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { members } = useMembers(space);
  const { openMembers } = useMembersSheet();

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
      <div className="page-bar">
        <Link to={`/s/${space}`} className="page-bar-crumb">
          ‹ <b>{isPersonal ? "Personal" : space}</b>
        </Link>
        <span className="page-bar-spacer" />
        {!isPersonal && members && (
          <AvatarStack
            names={members.members.map((member) => member.display_name)}
            onClick={() => openMembers(space)}
            ariaLabel={`Members of ${space}`}
          />
        )}
        {!isProtected && (
          <Link to={`/s/${space}/e/${page.path}`} className="page-bar-edit">
            Edit
          </Link>
        )}
      </div>

      <div className="reading">
        <h1 className="reading-title">{page.title || page.path}</h1>
        <p className="reading-meta">
          {pageMetaSentence({
            space,
            personal: isPersonal,
            lastEditor: page.last_editor,
            updated: page.updated,
            version: page.version,
          })}
        </p>
        {isProtected && (
          <p className="disclosure">
            Protected page — the protocol and persona pages update only
            through their own dedicated tool, not this editor.
          </p>
        )}
        <div
          className="page-body reading-body"
          // Safe: renderMarkdown always runs its output through DOMPurify.
          dangerouslySetInnerHTML={{ __html: renderMarkdown(page.body, space) }}
        />
        {page.tags.length > 0 && (
          <div className="reading-tags">
            {page.tags.map((tag) => (
              <span key={tag} className="reading-tag">
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
