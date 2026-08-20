/**
 * A single page, rendered from markdown to sanitized HTML.
 *
 * Pages under `meta/` (protocol, persona) are protected: they update only
 * through their own dedicated tool, never the web editor, so this view
 * shows a note instead of an Edit link for them.
 *
 * The page bar's avatar stack reuses the same `useMembers` +
 * `useMembersSheet` mechanism as `CoveView`'s whobar and `Sidebar`'s
 * active-cove stack, so clicking it opens the one shared `MembersSheet`
 * (owned by `AppShell`) for this page's cove. The personal cove has no
 * membership to administer — `useMembers` already returns `null` for it —
 * so the stack simply doesn't render there, per the brief's "the user's
 * own avatar or none" allowance.
 *
 * The cove's own organism sits before the crumb, in its own hue — the same
 * mark `Sidebar`'s rows and `Home`'s cards carry, so the page announces
 * which cove it belongs to by the creature, not by a dot that every cove
 * wears the same shape of.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, apiGet, apiSend } from "../api";
import { AvatarStack } from "../components/Avatar";
import { pageMetaSentence } from "../components/pageMeta";
import { CoveGlyph } from "../components/coveGlyph";
import { useCoveLook } from "../useAppearance";
import { renderMarkdown } from "../markdown";
import { useIndex } from "../IndexProvider";
import type { Page } from "../types";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";

/** Path prefix reserved for protocol/persona pages; not editable here. */
const PROTECTED_PREFIX = "meta/";

export default function PageView() {
  const { cove = "", "*": path = "" } = useParams<{ cove: string; "*": string }>();
  const isPersonal = cove === "personal";

  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { members } = useMembers(cove);
  const { openMembers } = useMembersSheet();
  const { refresh } = useIndex();
  const navigate = useNavigate();
  // Above the early returns below: this is a hook, so calling it after a
  // conditional `return` would change the hook order between the loading
  // and loaded renders.
  const { hue, family } = useCoveLook()(cove);

  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function removePage(pagePath: string) {
    setDeleting(true);
    setDeleteError(null);
    try {
      await apiSend("DELETE", `/api/pages/${cove}/${pagePath}`);
      await refresh();
      navigate(`/s/${cove}`);
    } catch (failure) {
      setDeleting(false);
      setDeleteError(
        failure instanceof ApiError
          ? failure.detail || failure.message
          : "could not delete that page",
      );
    }
  }

  useEffect(() => {
    let cancelled = false;
    setPage(null);
    setError(null);
    apiGet<Page>(`/api/pages/${cove}/${path}`)
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
  }, [cove, path]);

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
        <span className="page-bar-glyph" aria-hidden="true">
          <CoveGlyph alias={cove} color={hue.base} size={18} family={family} />
        </span>
        <Link to={`/s/${cove}`} className="page-bar-crumb">
          ‹ <b>{isPersonal ? "Personal" : cove}</b>
        </Link>
        <span className="page-bar-cover" />
        {!isPersonal && members && (
          <AvatarStack
            people={members.members.map((member) => ({
              name: member.display_name,
              src: member.avatar,
            }))}
            onClick={() => openMembers(cove)}
            ariaLabel={`Members of ${cove}`}
          />
        )}
        {!isProtected && (
          <Link to={`/s/${cove}/e/${page.path}`} className="page-bar-edit">
            Edit
          </Link>
        )}
      </div>

      <div className="reading">
        <h1 className="reading-title">{page.title || page.path}</h1>
        <p className="reading-meta">
          {pageMetaSentence({
            cove,
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
          dangerouslySetInnerHTML={{ __html: renderMarkdown(page.body, cove) }}
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

      {/* At the foot, behind a confirm, and absent for meta/ — the same
          shape CoveView gives leaving or destroying a cove. */}
      {!isProtected && (
        <section className="page-danger">
          {deleteError && <div className="notice">{deleteError}</div>}
          {confirmingDelete ? (
            <>
              <p className="muted">
                This deletes <b>{page.title || page.path}</b> and its whole
                history, permanently. Files attached to it stay in the cove.
              </p>
              <div className="ed-toolbar">
                <button
                  type="button"
                  className="danger"
                  disabled={deleting}
                  onClick={() => removePage(page.path)}
                >
                  {deleting ? "Deleting…" : "Delete permanently"}
                </button>
                <button
                  type="button"
                  disabled={deleting}
                  onClick={() => setConfirmingDelete(false)}
                >
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <button
              type="button"
              className="page-delete"
              onClick={() => setConfirmingDelete(true)}
            >
              Delete this page…
            </button>
          )}
        </section>
      )}
    </div>
  );
}
