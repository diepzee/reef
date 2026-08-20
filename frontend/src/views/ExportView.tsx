/** Download current cove content or a complete personal data archive. */

import { useState } from "react";

import { ApiError, apiDownload, apiSend } from "../api";
import { useIndex } from "../IndexProvider";

type ExportFormat = "markdown" | "json";

export default function ExportView() {
  const { rawIndex: index, error } = useIndex();
  const [scope, setScope] = useState("all");
  const [format, setFormat] = useState<ExportFormat>("markdown");
  const [showDelete, setShowDelete] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<null | "export" | "dump">(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  /**
   * Fetch one of the export routes and hand the bytes to the browser.
   *
   * They are POSTs so they carry the CSRF header, so a link cannot fetch
   * them; see `apiDownload`.
   */
  async function download(
    which: "export" | "dump",
    path: string,
    body: unknown,
    fallbackName: string,
  ) {
    setDownloading(which);
    setDownloadError(null);
    try {
      await apiDownload(path, body, fallbackName);
    } catch (problem) {
      setDownloadError(
        problem instanceof ApiError
          ? problem.message
          : "could not build your download",
      );
    } finally {
      setDownloading(null);
    }
  }

  async function deleteMyData() {
    setDeleting(true);
    setDeleteError(null);
    try {
      const result = await apiSend<{
        deleted: boolean;
        logout_url?: string;
      }>("POST", "/api/account/delete", {
        acknowledge_shared: acknowledged,
        confirmation,
      });
      window.location.href = result.logout_url ?? "/app/signed-out?deleted=1";
    } catch (error) {
      setDeleting(false);
      setDeleteError(
        error instanceof ApiError ? error.message : "could not delete your data",
      );
    }
  }

  return (
    <div>
      <div className="export-head">
        <h1>Export</h1>
        <p className="muted">Take your reef with you in open, inspectable files.</p>
      </div>

      {error && <div className="notice">{error}</div>}

      <section className="export-card">
        <div className="export-card-copy">
          <h2>Portable content export</h2>
          <p>
            Export the latest version of every page in one cove or across your whole
            reef. Stored files are listed as metadata; their bytes are included in the
            full dump below.
          </p>
        </div>

        <div className="export-fields">
          <label>
            Coves
            <select value={scope} onChange={(event) => setScope(event.target.value)}>
              <option value="all">All coves</option>
              {index?.coves.map((cove) => (
                <option key={cove.alias} value={cove.alias}>
                  {cove.alias}
                </option>
              ))}
            </select>
          </label>

          <label>
            Format
            <select
              value={format}
              onChange={(event) => setFormat(event.target.value as ExportFormat)}
            >
              <option value="markdown">Markdown ZIP</option>
              <option value="json">JSON</option>
            </select>
          </label>
        </div>

        <div className="export-format-note muted">
          {format === "markdown"
            ? "Import-compatible Markdown pages with YAML frontmatter."
            : "Current page bodies and metadata in one machine-readable document."}
        </div>

        {downloadError && <div className="notice">{downloadError}</div>}

        <button
          type="button"
          className="button export-download"
          disabled={downloading !== null}
          onClick={() =>
            download("export", "/api/export", { scope, format }, "reef-export.zip")
          }
        >
          {downloading === "export" ? "Preparing…" : "Download export"}
        </button>
      </section>

      <section className="export-card export-dump-card">
        <div className="export-dump-mark" aria-hidden="true">
          ↓
        </div>
        <div className="export-card-copy">
          <h2>Dump my data</h2>
          <p>
            One ZIP containing every cove you can access: current Markdown pages, the
            raw index, full revision history, stored file bytes and metadata, member
            names, and your sharing audit trail.
          </p>
          <p className="muted export-caveat">
            Shared-cove content is included because it is part of the reef you can see;
            keep the archive private.
          </p>
        </div>
        <button
          type="button"
          className="button export-dump-button"
          disabled={downloading !== null}
          onClick={() =>
            download("dump", "/api/export/dump", {}, "reef-my-data.zip")
          }
        >
          {downloading === "dump" ? "Building your archive…" : "Dump my data"}
        </button>
      </section>

      <section className="delete-zone">
        <div className="export-card-copy">
          <h2>Danger zone</h2>
          <p>
            Permanently delete your account, personal cove, and shared coves where
            you are the only member.
          </p>
        </div>

        {!showDelete ? (
          <button type="button" className="delete-reveal" onClick={() => setShowDelete(true)}>
            Delete my data…
          </button>
        ) : (
          <div className="delete-guards">
            <p>
              Shared coves with other members will remain. Ownership transfers to
              another member, your membership is removed, and your author identity is
              cleared from retained history. Everything else is permanent.
            </p>
            <label className="delete-ack">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => setAcknowledged(event.target.checked)}
              />
              <span>I understand this cannot be undone and have exported anything I need.</span>
            </label>
            <label className="delete-phrase">
              Type <strong>DELETE</strong> to confirm
              <input
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            {deleteError && <div className="notice">{deleteError}</div>}
            <div className="delete-actions">
              <button
                type="button"
                className="delete-final"
                disabled={!acknowledged || confirmation !== "DELETE" || deleting}
                onClick={deleteMyData}
              >
                {deleting ? "Deleting…" : "Permanently delete my data"}
              </button>
              <button
                type="button"
                className="delete-cancel"
                disabled={deleting}
                onClick={() => {
                  setShowDelete(false);
                  setAcknowledged(false);
                  setConfirmation("");
                  setDeleteError(null);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
