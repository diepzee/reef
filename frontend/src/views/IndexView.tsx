/** Human-readable access to the same body-free index the assistant uses. */

import { Link } from "react-router-dom";

import { CoveGlyph } from "../components/coveGlyph";
import { useIndex } from "../IndexProvider";
import { useCoveLook } from "../useAppearance";

export default function IndexView() {
  const { rawIndex: index, error } = useIndex();
  const look = useCoveLook();
  const pageCount = index?.coves.reduce((total, cove) => total + cove.pages.length, 0) ?? 0;

  return (
    <div>
      <div className="index-head">
        <div>
          <h1>Index</h1>
          {index && (
            <p className="muted">
              {index.coves.length} {index.coves.length === 1 ? "cove" : "coves"} ·{" "}
              {pageCount} {pageCount === 1 ? "page" : "pages"} · page bodies omitted
            </p>
          )}
        </div>
        <Link to="/" className="index-back">
          View coves
        </Link>
      </div>

      {error && <div className="notice">{error}</div>}
      {!error && index === null && <p className="muted">Loading…</p>}

      {index?.coves.map((cove) => (
        <section key={cove.alias} className="index-cove">
          <div className="index-cove-head">
            <span className="index-cove-glyph" aria-hidden="true">
              <CoveGlyph
                alias={cove.alias}
                color={look(cove.alias).hue.base}
                size={18}
                family={look(cove.alias).family}
              />
            </span>
            <h2>
              <Link to={`/s/${cove.alias}`}>{cove.alias}</Link>
            </h2>
            <span className="muted">
              v{cove.version} · {cove.pages.length} {cove.pages.length === 1 ? "page" : "pages"}
            </span>
          </div>

          {cove.pages.length === 0 ? (
            <p className="muted index-empty">No pages.</p>
          ) : (
            <ul className="index-pages">
              {cove.pages.map((page) => (
                <li key={page.path} className="index-page">
                  <div className="index-page-titleline">
                    <Link to={`/s/${cove.alias}/p/${page.path}`} className="index-page-title">
                      {page.title || page.path}
                    </Link>
                    <code>{page.path}</code>
                  </div>
                  {page.description && <p>{page.description}</p>}
                  <div className="index-page-meta muted">
                    <span>v{page.version}</span>
                    <span>{page.size.toLocaleString()} characters</span>
                    {page.tags.map((tag) => (
                      <span key={tag} className="index-tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                  {page.references.length > 0 && (
                    <div className="index-references">
                      <span className="muted">References</span>
                      {page.references.map((reference) => (
                        <Link
                          key={`${reference.cove}:${reference.path}`}
                          to={`/s/${reference.cove}/p/${reference.path}`}
                        >
                          {reference.cove === cove.alias
                            ? reference.path
                            : `${reference.cove}:${reference.path}`}
                        </Link>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          {cove.attachments.length > 0 && (
            <details className="index-attachments">
              <summary>
                {cove.attachments.length}{" "}
                {cove.attachments.length === 1 ? "file" : "files"}
              </summary>
              <ul>
                {cove.attachments.map((attachment) => (
                  <li key={attachment.key}>
                    <a
                      href={`/api/files/${encodeURIComponent(cove.alias)}/${attachment.key
                        .split("/")
                        .map(encodeURIComponent)
                        .join("/")}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {attachment.filename}
                    </a>{" "}
                    <span className="muted">
                      {attachment.mime} · {attachment.size.toLocaleString()} bytes
                    </span>
                    {attachment.description && <div>{attachment.description}</div>}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      ))}

      {index && (
        <details className="raw-index">
          <summary>Raw index JSON</summary>
          <pre>{JSON.stringify(index, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
