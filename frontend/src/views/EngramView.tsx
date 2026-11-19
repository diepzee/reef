/**
 * A single engram, rendered from markdown to sanitized HTML.
 *
 * Engrams under `meta/` (protocol, persona) are protected: they update only
 * through their own dedicated tool, never the web editor, so this view
 * shows a note instead of an Edit link for them.
 *
 * The page bar's avatar stack reuses the same `useMembers` +
 * `useMembersSheet` mechanism as `SpaceView`'s whobar and `Sidebar`'s
 * active-space stack, so clicking it opens the one shared `MembersSheet`
 * (owned by `AppShell`) for this engram's space. The personal space has no
 * membership to administer — `useMembers` already returns `null` for it —
 * so the stack simply doesn't render there, per the brief's "the user's
 * own avatar or none" allowance.
 *
 * The space's own organism sits before the crumb, in its own hue — the same
 * mark `Sidebar`'s rows and `Home`'s cards carry, so the engram announces
 * which space it belongs to by the creature, not by a dot that every space
 * wears the same shape of.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError, apiGet, apiSend } from "../api";
import { AvatarStack } from "../components/Avatar";
import { engramMetaSentence } from "../components/engramMeta";
import { SpaceGlyph } from "../components/spaceGlyph";
import { useSpaceLook } from "../useAppearance";
import { renderMarkdown } from "../markdown";
import { useIndex } from "../IndexProvider";
import type { Engram } from "../types";
import { useMembers } from "../useMembers";
import { useMembersSheet } from "../useMembersSheet";

/** Path prefix reserved for protocol/persona engrams; not editable here. */
const PROTECTED_PREFIX = "meta/";

export default function EngramView() {
  const { space = "", "*": path = "" } = useParams<{ space: string; "*": string }>();
  const isPersonal = space === "personal";

  const [engram, setEngram] = useState<Engram | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { members } = useMembers(space);
  const { openMembers } = useMembersSheet();
  const { refresh } = useIndex();
  const navigate = useNavigate();
  // Above the early returns below: this is a hook, so calling it after a
  // conditional `return` would change the hook order between the loading
  // and loaded renders.
  const { hue, family } = useSpaceLook()(space);

  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function removeEngram(engramPath: string) {
    setDeleting(true);
    setDeleteError(null);
    try {
      await apiSend("DELETE", `/api/engrams/${space}/${engramPath}`);
      await refresh();
      navigate(`/s/${space}`);
    } catch (failure) {
      setDeleting(false);
      setDeleteError(
        failure instanceof ApiError
          ? failure.detail || failure.message
          : "could not delete that engram",
      );
    }
  }

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

  const isProtected = engram.path.startsWith(PROTECTED_PREFIX);

  return (
    <div>
      <div className="page-bar">
        <span className="page-bar-glyph" aria-hidden="true">
          <SpaceGlyph alias={space} color={hue.base} size={18} family={family} />
        </span>
        <Link to={`/s/${space}`} className="page-bar-crumb">
          ‹ <b>{isPersonal ? "Personal" : space}</b>
        </Link>
        <span className="page-bar-cover" />
        {!isPersonal && members && (
          <AvatarStack
            people={members.members.map((member) => ({
              name: member.display_name,
              src: member.avatar,
            }))}
            onClick={() => openMembers(space)}
            ariaLabel={`Members of ${space}`}
          />
        )}
        {!isProtected && (
          <Link to={`/s/${space}/e/${engram.path}`} className="page-bar-edit">
            Edit
          </Link>
        )}
      </div>

      <div className="reading">
        <h1 className="reading-title">{engram.title || engram.path}</h1>
        <p className="reading-meta">
          {engramMetaSentence({
            space,
            personal: isPersonal,
            lastEditor: engram.last_editor,
            updated: engram.updated,
            version: engram.version,
          })}
        </p>
        {isProtected && (
          <p className="disclosure">
            Protected engram — the protocol and persona engrams update only
            through their own dedicated tool, not this editor.
          </p>
        )}
        <div
          className="page-body reading-body"
          // Safe: renderMarkdown always runs its output through DOMPurify.
          dangerouslySetInnerHTML={{ __html: renderMarkdown(engram.body, space) }}
        />
        {engram.tags.length > 0 && (
          <div className="reading-tags">
            {engram.tags.map((tag) => (
              <span key={tag} className="reading-tag">
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* At the foot, behind a confirm, and absent for meta/ — the same
          shape SpaceView gives leaving or destroying a space. */}
      {!isProtected && (
        <section className="page-danger">
          {deleteError && <div className="notice">{deleteError}</div>}
          {confirmingDelete ? (
            <>
              <p className="muted">
                This deletes <b>{engram.title || engram.path}</b> and its whole
                history, permanently. Files attached to it stay in the space.
              </p>
              <div className="ed-toolbar">
                <button
                  type="button"
                  className="danger"
                  disabled={deleting}
                  onClick={() => removeEngram(engram.path)}
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
              Delete this engram…
            </button>
          )}
        </section>
      )}
    </div>
  );
}
