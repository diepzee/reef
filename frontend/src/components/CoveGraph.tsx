/** A deterministic, dependency-free SVG map of coves and their references. */

import { useState } from "react";
import { Link } from "react-router-dom";

import { coveConnections } from "../coveGraph";
import type { SpaceIndex } from "../types";
import { spaceColor } from "./spaceColor";

interface Point {
  x: number;
  y: number;
}

const WIDTH = 960;
const HEIGHT = 600;
const NODE_RADIUS = 58;

/** Arrange every cove around an ellipse, with useful two- and one-node layouts. */
function layout(spaces: SpaceIndex[]): Map<string, Point> {
  if (spaces.length === 1) {
    return new Map([[spaces[0]!.alias, { x: WIDTH / 2, y: HEIGHT / 2 }]]);
  }
  if (spaces.length === 2) {
    return new Map([
      [spaces[0]!.alias, { x: 260, y: HEIGHT / 2 }],
      [spaces[1]!.alias, { x: 700, y: HEIGHT / 2 }],
    ]);
  }
  return new Map(
    spaces.map((space, index) => {
      const angle = -Math.PI / 2 + (index * Math.PI * 2) / spaces.length;
      return [
        space.alias,
        {
          x: WIDTH / 2 + Math.cos(angle) * 350,
          y: HEIGHT / 2 + Math.sin(angle) * 210,
        },
      ];
    }),
  );
}

/** Clip a connection to the edge of the circular node, not its centre. */
function clippedPoint(from: Point, toward: Point): Point {
  const dx = toward.x - from.x;
  const dy = toward.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  return {
    x: from.x + (dx / length) * NODE_RADIUS,
    y: from.y + (dy / length) * NODE_RADIUS,
  };
}

function shortAlias(alias: string): string {
  return alias.length > 13 ? `${alias.slice(0, 11)}…` : alias;
}

export function CoveGraph({ spaces }: { spaces: SpaceIndex[] }) {
  const connections = coveConnections(spaces);
  const positions = layout(spaces);
  const [active, setActive] = useState<string | null>(null);

  return (
    <div className="graph-wrap">
      <div className="graph-scroll">
        <svg
          className="cove-graph"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-labelledby="cove-graph-title cove-graph-desc"
        >
          <title id="cove-graph-title">Cove reference graph</title>
          <desc id="cove-graph-desc">
            Coves are nodes. Arrows show wiki references from pages in one cove to pages
            in another.
          </desc>
          <defs>
            {connections.map((connection, index) => (
              <marker
                key={`${connection.source}-${connection.target}`}
                id={`cove-arrow-${index}`}
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill={spaceColor(connection.source).base} />
              </marker>
            ))}
          </defs>

          <g aria-hidden="true">
            {connections.map((connection, index) => {
              const source = positions.get(connection.source)!;
              const target = positions.get(connection.target)!;
              const start = clippedPoint(source, target);
              const end = clippedPoint(target, source);
              const reverse = connections.some(
                (candidate) =>
                  candidate.source === connection.target &&
                  candidate.target === connection.source,
              );
              const dx = end.x - start.x;
              const dy = end.y - start.y;
              const length = Math.hypot(dx, dy) || 1;
              // Even a one-way reference gets a relaxed curve; reciprocal
              // references bend away from one another a little further.
              const bend = reverse ? 52 : 28;
              const control = {
                x: (start.x + end.x) / 2 - (dy / length) * bend,
                y: (start.y + end.y) / 2 + (dx / length) * bend,
              };
              const isDimmed =
                active !== null && active !== connection.source && active !== connection.target;
              const label = `${connection.source} to ${connection.target}: ${connection.referenceCount} ${
                connection.referenceCount === 1 ? "reference" : "references"
              } from ${connection.sourcePageCount} ${
                connection.sourcePageCount === 1 ? "page" : "pages"
              }`;

              return (
                <g
                  key={`${connection.source}-${connection.target}`}
                  className={`graph-edge ${isDimmed ? "graph-dimmed" : ""}`}
                >
                  <title>{label}</title>
                  <path
                    d={`M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`}
                    stroke={spaceColor(connection.source).base}
                    strokeWidth={Math.min(5, 2 + Math.log2(connection.referenceCount))}
                    markerEnd={`url(#cove-arrow-${index})`}
                  />
                  <circle cx={control.x} cy={control.y} r="12" className="graph-edge-count-bg" />
                  <text x={control.x} y={control.y + 4} className="graph-edge-count">
                    {connection.referenceCount}
                  </text>
                </g>
              );
            })}
          </g>

          {spaces.map((space) => {
            const point = positions.get(space.alias)!;
            const hue = spaceColor(space.alias);
            const isDimmed = active !== null && active !== space.alias;
            return (
              <Link
                key={space.alias}
                to={`/s/${space.alias}`}
                className={`graph-node-link ${isDimmed ? "graph-dimmed" : ""}`}
                onMouseEnter={() => setActive(space.alias)}
                onMouseLeave={() => setActive(null)}
                onFocus={() => setActive(space.alias)}
                onBlur={() => setActive(null)}
              >
                <g transform={`translate(${point.x} ${point.y})`}>
                  <title>
                    {space.alias}, {space.pages.length} {space.pages.length === 1 ? "page" : "pages"}
                  </title>
                  <circle r={NODE_RADIUS} className="graph-node" style={{ stroke: hue.base }} />
                  <circle cy="-29" r="5" fill={hue.base} />
                  <text y="-5" className="graph-node-name">
                    {shortAlias(space.alias)}
                  </text>
                  <text y="18" className="graph-node-count">
                    {space.pages.length} {space.pages.length === 1 ? "page" : "pages"}
                  </text>
                </g>
              </Link>
            );
          })}
        </svg>
      </div>

      <p className="graph-key muted">
        Arrows follow <code>[[cove:page.md]]</code> references. The number is the count
        of resolved page references.
      </p>
      {connections.length === 0 ? (
        <p className="graph-empty muted">No cross-cove references yet.</p>
      ) : (
        <ul className="sr-only">
          {connections.map((connection) => (
            <li key={`${connection.source}-${connection.target}`}>
              {connection.source} references {connection.target} {connection.referenceCount}{" "}
              {connection.referenceCount === 1 ? "time" : "times"} from{" "}
              {connection.sourcePageCount} {connection.sourcePageCount === 1 ? "page" : "pages"}.
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
