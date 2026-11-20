/**
 * Dev-only organism gallery: every family × a seed sweep × the hue
 * palette, at tile (64px) and chip (20px) sizes — the tuning surface for
 * the generators' parameter ranges in `organisms.ts`. The route is gated
 * on NODE_ENV, so production builds never mount it.
 */

import { FAMILIES, generateFamily, type OrganismPath } from "../components/organisms";
import { spaceColor } from "../components/spaceColor";

const SEEDS = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89];
const HUES = ["h1", "h2", "h3", "h4", "h5", "h6", "h7"].map((a) => spaceColor(a).base);

function Paths({ paths }: { paths: readonly OrganismPath[] }) {
  return (
    <>
      {paths.map((p, i) =>
        p.stroke !== undefined ? (
          <path
            key={i}
            d={p.d}
            fill="none"
            stroke="currentColor"
            strokeWidth={p.stroke}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ) : (
          <path key={i} d={p.d} fill="currentColor" fillRule={p.evenodd ? "evenodd" : "nonzero"} />
        ),
      )}
    </>
  );
}

function Specimen({ paths, hue }: { paths: readonly OrganismPath[]; hue: string }) {
  return (
    <div
      style={{
        color: hue,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 4,
        background: "var(--surface, #fff)",
        borderRadius: 8,
        padding: 6,
      }}
    >
      <svg viewBox="0 0 64 64" width={64} height={64}>
        <Paths paths={paths} />
      </svg>
      <svg viewBox="0 0 64 64" width={20} height={20}>
        <Paths paths={paths} />
      </svg>
    </div>
  );
}

export function Gallery() {
  return (
    <div style={{ padding: 24, display: "grid", gap: 24 }}>
      {FAMILIES.map((fam) => (
        <section key={fam}>
          <h2 style={{ marginBottom: 8 }}>{fam}</h2>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
            {SEEDS.map((seed, i) => (
              <Specimen
                key={seed}
                paths={generateFamily(fam, seed).paths}
                hue={HUES[i % HUES.length]!}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
