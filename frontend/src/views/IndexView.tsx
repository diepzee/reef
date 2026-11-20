/** Human-readable access to the same body-free index the assistant uses. */

import { Link } from "react-router-dom";

import { SpaceGlyph } from "../components/spaceGlyph";
import { useIndex } from "../IndexProvider";
import { useSpaceLook } from "../useAppearance";

export default function IndexView() {
  const { rawIndex: index, error } = useIndex();
  const look = useSpaceLook();
  const engramCount = index?.spaces.reduce((total, space) => total + space.engrams.length, 0) ?? 0;

  return (
    <div>
      <div className="index-head">
        <div>
          <h1>Index</h1>
          {index && (
            <p className="muted">
              {index.spaces.length} {index.spaces.length === 1 ? "space" : "spaces"} ·{" "}
              {engramCount} {engramCount === 1 ? "engram" : "engrams"} · engram bodies omitted
            </p>
          )}
        </div>
        <Link to="/" className="index-back">
          View spaces
        </Link>
      </div>

      {error && <div className="notice">{error}</div>}
      {!error && index === null && <p className="muted">Loading…</p>}

      {index?.spaces.map((space) => (
        <section key={space.alias} className="index-space">
          <div className="index-space-head">
            <span className="index-space-glyph" aria-hidden="true">
              <SpaceGlyph
                alias={space.alias}
                color={look(space.alias).hue.base}
                size={18}
                family={look(space.alias).family}
              />
            </span>
            <h2>
              <Link to={`/s/${space.alias}`}>{space.alias}</Link>
            </h2>
            <span className="muted">
              v{space.version} · {space.engrams.length} {space.engrams.length === 1 ? "engram" : "engrams"}
            </span>
          </div>

          {space.engrams.length === 0 ? (
            <p className="muted index-empty">No engrams.</p>
          ) : (
            <ul className="index-pages">
              {space.engrams.map((engram) => (
                <li key={engram.path} className="index-page">
                  <div className="index-page-titleline">
                    <Link to={`/s/${space.alias}/engram/${engram.path}`} className="index-page-title">
                      {engram.title || engram.path}
                    </Link>
                    <code>{engram.path}</code>
                  </div>
                  {engram.description && <p>{engram.description}</p>}
                  <div className="index-page-meta muted">
                    <span>v{engram.version}</span>
                    <span>{engram.size.toLocaleString()} characters</span>
                    {engram.tags.map((tag) => (
                      <span key={tag} className="index-tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                  {engram.references.length > 0 && (
                    <div className="index-references">
                      <span className="muted">References</span>
                      {engram.references.map((reference) => (
                        <Link
                          key={`${reference.space}:${reference.path}`}
                          to={`/s/${reference.space}/engram/${reference.path}`}
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
