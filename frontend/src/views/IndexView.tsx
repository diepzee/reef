/** Human-readable access to the same body-free index the assistant uses. */

import { Link } from "react-router-dom";

import { useIndex } from "../IndexProvider";

export default function IndexView() {
  const { rawIndex: index, error } = useIndex();
  const pageCount = index?.spaces.reduce((total, space) => total + space.pages.length, 0) ?? 0;

  return (
    <div>
      <div className="index-head">
        <div>
          <h1>Index</h1>
          {index && (
            <p className="muted">
              {index.spaces.length} {index.spaces.length === 1 ? "cove" : "coves"} ·{" "}
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

      {index?.spaces.map((space) => (
        <section key={space.alias} className="index-cove">
          <div className="index-cove-head">
            <h2>
              <Link to={`/s/${space.alias}`}>{space.alias}</Link>
            </h2>
            <span className="muted">
              v{space.version} · {space.pages.length} {space.pages.length === 1 ? "page" : "pages"}
            </span>
          </div>

          {space.pages.length === 0 ? (
            <p className="muted index-empty">No pages.</p>
          ) : (
            <ul className="index-pages">
              {space.pages.map((page) => (
                <li key={page.path} className="index-page">
                  <div className="index-page-titleline">
                    <Link to={`/s/${space.alias}/p/${page.path}`} className="index-page-title">
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
                          key={`${reference.space}:${reference.path}`}
                          to={`/s/${reference.space}/p/${reference.path}`}
                        >
                          {reference.space === space.alias
                            ? reference.path
                            : `${reference.space}:${reference.path}`}
                        </Link>
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          {space.attachments.length > 0 && (
            <details className="index-attachments">
              <summary>
                {space.attachments.length}{" "}
                {space.attachments.length === 1 ? "file" : "files"}
              </summary>
              <ul>
                {space.attachments.map((attachment) => (
                  <li key={attachment.key}>
                    <a
                      href={`/api/files/${encodeURIComponent(space.alias)}/${attachment.key
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
