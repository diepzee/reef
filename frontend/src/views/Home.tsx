/**
 * The landing view: every space the principal can see, as tappable cards.
 *
 * `GET /api/index` already orders the personal space first, so this view
 * only renders what comes back — no client-side sort.
 *
 * Each card carries the identity pass's per-space hue: a gradient stripe
 * across the top, a tinted frond chip, and (for shared spaces) a small
 * avatar stack of that space's members via `useMembers`. `useMembers` is
 * module-cached per space alias (see its docstring), so one call per card
 * here dedupes against any other mounted consumer of the same space
 * (Sidebar, SpaceView, PageView) rather than issuing a fresh fetch — an
 * explicitly accepted per-card call, not an N+1 (space counts are small).
 * The personal space has no membership to administer, so its card shows
 * "only you" instead of a stack, matching every other "only you" surface.
 */

import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";

import { AvatarStack } from "../components/Avatar";
import { SpaceGlyph } from "../components/spaceGlyph";
import { spaceColor } from "../components/spaceColor";
import { useIndex } from "../IndexProvider";
import { useMembers } from "../useMembers";
import { getSpacesView, setSpacesView, type SpacesView } from "../spacesView";
import type { SpaceIndex } from "../types";

/** One space's card: hue stripe, tinted frond chip, alias, subline, and (shared spaces) its member stack. */
function SpaceCard({ space }: { space: SpaceIndex }) {
  const isPersonal = space.alias === "personal";
  const hue = spaceColor(space.alias);
  const { members } = useMembers(space.alias);
  const pageCount = space.pages.length;

  return (
    <Link
      to={`/s/${space.alias}`}
      className="card space-card"
      style={{ "--hue-base": hue.base, "--hue-light": hue.light } as CSSProperties}
    >
      <span className="space-card-stripe" aria-hidden="true" />
      <span className="space-card-row">
        <span className="space-card-chip" aria-hidden="true">
          <SpaceGlyph alias={space.alias} color={hue.base} />
        </span>
        <span className="space-card-text">
          <span className="space-card-alias">
            {space.alias}
          </span>
          <span className="space-card-sub muted">
            {pageCount} page{pageCount === 1 ? "" : "s"}
            {isPersonal ? " · only you" : ""}
          </span>
        </span>
        {!isPersonal && members && (
          <AvatarStack
            names={members.members.map((member) => member.display_name)}
            size="sm"
            ariaLabel={`Members of ${space.alias}`}
          />
        )}
      </span>
    </Link>
  );
}

/** One space as a grid tile: coral glyph in a circular hue "pool", alias, subline. */
function SpaceTile({ space }: { space: SpaceIndex }) {
  const isPersonal = space.alias === "personal";
  const hue = spaceColor(space.alias);
  const { members } = useMembers(space.alias);
  const pageCount = space.pages.length;

  return (
    <Link
      to={`/s/${space.alias}`}
      className="card space-tile"
      style={{ "--hue-base": hue.base, "--hue-light": hue.light } as CSSProperties}
    >
      <span className="space-tile-pool" aria-hidden="true">
        <SpaceGlyph alias={space.alias} color={hue.base} size={24} />
      </span>
      <span className="space-card-alias">{isPersonal ? "Personal" : space.alias}</span>
      <span className="space-card-sub muted">
        {pageCount} page{pageCount === 1 ? "" : "s"}
        {isPersonal ? " · only you" : ""}
      </span>
      {!isPersonal && members && (
        <AvatarStack
          names={members.members.map((member) => member.display_name)}
          size="sm"
          ariaLabel={`Members of ${space.alias}`}
        />
      )}
    </Link>
  );
}

export default function Home() {
  const { index, error } = useIndex();
  const spaces = index?.spaces ?? null;
  const [view, setView] = useState<SpacesView>(getSpacesView);

  function pick(next: SpacesView) {
    setView(next);
    setSpacesView(next);
  }

  return (
    <div>
      <div className="spaces-head">
        <h1>Your reef&rsquo;s coves</h1>
        <div className="segview" role="tablist" aria-label="View">
          <button
            type="button"
            role="tab"
            className={`seg ${view === "list" ? "seg-active" : ""}`}
            aria-selected={view === "list"}
            aria-label="List view"
            onClick={() => pick("list")}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M2 4h12M2 8h12M2 12h12"
                stroke="currentColor"
                strokeWidth="1.9"
                strokeLinecap="round"
                fill="none"
              />
            </svg>
          </button>
          <button
            type="button"
            role="tab"
            className={`seg ${view === "grid" ? "seg-active" : ""}`}
            aria-selected={view === "grid"}
            aria-label="Tile view"
            onClick={() => pick("grid")}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
              <g fill="currentColor">
                <rect x="2" y="2" width="5.2" height="5.2" rx="1.4" />
                <rect x="8.8" y="2" width="5.2" height="5.2" rx="1.4" />
                <rect x="2" y="8.8" width="5.2" height="5.2" rx="1.4" />
                <rect x="8.8" y="8.8" width="5.2" height="5.2" rx="1.4" />
              </g>
            </svg>
          </button>
        </div>
      </div>
      {error && <div className="notice">{error}</div>}
      {!error && spaces === null && <p className="muted">Loading…</p>}
      {spaces !== null && spaces.length === 0 && (
        <p className="muted">No spaces yet.</p>
      )}
      {view === "list" ? (
        <>
          <ul className="card-list">
            {spaces?.map((space) => (
              <li key={space.alias}>
                <SpaceCard space={space} />
              </li>
            ))}
          </ul>
          <p>
            <Link to="/spaces/new" className="button">
              New cove
            </Link>
          </p>
        </>
      ) : (
        <ul className="tile-grid">
          {spaces?.map((space) => (
            <li key={space.alias}>
              <SpaceTile space={space} />
            </li>
          ))}
          <li>
            <Link to="/spaces/new" className="card space-tile space-tile-new">
              <span className="space-tile-plus" aria-hidden="true">
                +
              </span>
              New cove
            </Link>
          </li>
        </ul>
      )}
    </div>
  );
}
