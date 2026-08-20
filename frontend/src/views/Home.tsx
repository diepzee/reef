/**
 * The landing view: every cove the principal can see, as tappable cards.
 *
 * `GET /api/index` already orders the personal cove first, so this view
 * only renders what comes back — no client-side sort.
 *
 * Each card carries the identity pass's per-cove hue: a gradient stripe
 * across the top, a tinted frond chip, and (for shared coves) a small
 * avatar stack of that cove's members via `useMembers`. `useMembers` is
 * module-cached per cove alias (see its docstring), so one call per card
 * here dedupes against any other mounted consumer of the same cove
 * (Sidebar, CoveView, PageView) rather than issuing a fresh fetch — an
 * explicitly accepted per-card call, not an N+1 (cove counts are small).
 * The personal cove has no membership to administer, so its card shows
 * "only you" instead of a stack, matching every other "only you" surface.
 */

import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";

import { AvatarStack } from "../components/Avatar";
import { CoveGraph } from "../components/CoveGraph";
import { CoveGlyph } from "../components/coveGlyph";
import { useIndex } from "../IndexProvider";
import { useMembers } from "../useMembers";
import { getCovesView, setCovesView, type CovesView } from "../covesView";
import type { CoveIndex } from "../types";
import { useCoveLook } from "../useAppearance";

/** One cove's card: hue stripe, tinted frond chip, alias, subline, and (shared coves) its member stack. */
function CoveCard({ cove }: { cove: CoveIndex }) {
  const isPersonal = cove.alias === "personal";
  const { hue, family } = useCoveLook()(cove.alias);
  const { members } = useMembers(cove.alias);
  const pageCount = cove.pages.length;

  return (
    <Link
      to={`/s/${cove.alias}`}
      className="card cove-card"
      style={{ "--hue-base": hue.base, "--hue-light": hue.light } as CSSProperties}
    >
      <span className="cove-card-stripe" aria-hidden="true" />
      <span className="cove-card-row">
        <span className="cove-card-chip" aria-hidden="true">
          <CoveGlyph alias={cove.alias} color={hue.base} family={family} />
        </span>
        <span className="cove-card-text">
          <span className="cove-card-alias">
            {cove.alias}
          </span>
          <span className="cove-card-sub muted">
            {pageCount} page{pageCount === 1 ? "" : "s"}
            {isPersonal ? " · only you" : ""}
          </span>
        </span>
        {!isPersonal && members && (
          <AvatarStack
            people={members.members.map((member) => ({
              name: member.display_name,
              src: member.avatar,
            }))}
            size="sm"
            ariaLabel={`Members of ${cove.alias}`}
          />
        )}
      </span>
    </Link>
  );
}

/** One cove as a grid tile: coral glyph in a circular hue "pool", alias, subline. */
function CoveTile({ cove }: { cove: CoveIndex }) {
  const isPersonal = cove.alias === "personal";
  const { hue, family } = useCoveLook()(cove.alias);
  const { members } = useMembers(cove.alias);
  const pageCount = cove.pages.length;

  return (
    <Link
      to={`/s/${cove.alias}`}
      className="card cove-tile"
      style={{ "--hue-base": hue.base, "--hue-light": hue.light } as CSSProperties}
    >
      <span className="cove-tile-pool" aria-hidden="true">
        <CoveGlyph alias={cove.alias} color={hue.base} size={24} family={family} />
      </span>
      <span className="cove-card-alias">{isPersonal ? "Personal" : cove.alias}</span>
      <span className="cove-card-sub muted">
        {pageCount} page{pageCount === 1 ? "" : "s"}
        {isPersonal ? " · only you" : ""}
      </span>
      {!isPersonal && members && (
        <AvatarStack
          people={members.members.map((member) => ({
            name: member.display_name,
            src: member.avatar,
          }))}
          size="sm"
          ariaLabel={`Members of ${cove.alias}`}
        />
      )}
    </Link>
  );
}

export default function Home() {
  const { index, error } = useIndex();
  const coves = index?.coves ?? null;
  const [view, setView] = useState<CovesView>(getCovesView);

  function pick(next: CovesView) {
    setView(next);
    setCovesView(next);
  }

  return (
    <div>
      <div className="coves-head">
        <h1>Your <span className="reef-name">reef</span>&rsquo;s coves</h1>
        <Link to="/index" className="index-shortcut">
          Index
        </Link>
        <Link to="/export" className="index-shortcut">
          Export
        </Link>
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
          <button
            type="button"
            role="tab"
            className={`seg ${view === "graph" ? "seg-active" : ""}`}
            aria-selected={view === "graph"}
            aria-label="Graph view"
            onClick={() => pick("graph")}
          >
            <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M4 4.25 11.75 7.5M4 4.25 6.25 12M11.75 7.5 6.25 12"
                stroke="currentColor"
                strokeWidth="1.4"
                fill="none"
              />
              <circle cx="4" cy="4.25" r="2" fill="currentColor" />
              <circle cx="11.75" cy="7.5" r="2" fill="currentColor" />
              <circle cx="6.25" cy="12" r="2" fill="currentColor" />
            </svg>
          </button>
        </div>
      </div>
      {error && <div className="notice">{error}</div>}
      {!error && coves === null && <p className="muted">Loading…</p>}
      {coves !== null && coves.length === 0 && (
        <p className="muted">No coves yet.</p>
      )}
      {view === "list" && (
        <>
          <ul className="card-list">
            {coves?.map((cove) => (
              <li key={cove.alias}>
                <CoveCard cove={cove} />
              </li>
            ))}
          </ul>
          <p>
            <Link to="/coves/new" className="button">
              New cove
            </Link>
          </p>
        </>
      )}
      {view === "grid" && (
        <ul className="tile-grid">
          {coves?.map((cove) => (
            <li key={cove.alias}>
              <CoveTile cove={cove} />
            </li>
          ))}
          <li>
            <Link to="/coves/new" className="card cove-tile cove-tile-new">
              <span className="cove-tile-plus" aria-hidden="true">
                +
              </span>
              New cove
            </Link>
          </li>
        </ul>
      )}
      {view === "graph" && (
        <>
          {coves && <CoveGraph coves={coves} />}
          <p>
            <Link to="/coves/new" className="button">
              New cove
            </Link>
          </p>
        </>
      )}
    </div>
  );
}
