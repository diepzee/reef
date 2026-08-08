/**
 * The landing view: every space the principal can see, as tappable cards.
 *
 * `GET /api/index` already orders the personal space first, so this view
 * only renders what comes back — no client-side sort.
 */

import { Link } from "react-router-dom";

import { useIndex } from "../IndexProvider";

export default function Home() {
  const { index, error } = useIndex();
  const spaces = index?.spaces ?? null;

  return (
    <div>
      <h1>Spaces</h1>
      {error && <div className="notice">{error}</div>}
      {!error && spaces === null && <p className="muted">Loading…</p>}
      {spaces !== null && spaces.length === 0 && (
        <p className="muted">No spaces yet.</p>
      )}
      <ul className="card-list">
        {spaces?.map((space) => (
          <li key={space.alias}>
            <Link to={`/s/${space.alias}`} className="card space-card">
              <span className="space-card-alias">
                {space.alias === "personal" ? "Personal" : space.alias}
              </span>
              <span className="muted">
                {space.pages.length} page{space.pages.length === 1 ? "" : "s"}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <p>
        <Link to="/spaces/new" className="button">
          New space
        </Link>
      </p>
    </div>
  );
}
