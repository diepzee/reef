/**
 * The landing view: every space the principal can see, as tappable cards.
 *
 * `GET /api/index` already orders the personal space first, so this view
 * only renders what comes back — no client-side sort.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, apiGet } from "../api";
import type { IndexPayload, SpaceIndex } from "../types";

export default function Home() {
  const [spaces, setSpaces] = useState<SpaceIndex[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGet<IndexPayload>("/api/index")
      .then((index) => {
        if (!cancelled) setSpaces(index.spaces);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "could not load spaces");
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
