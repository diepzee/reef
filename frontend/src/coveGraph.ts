/** Pure graph projection from the index, kept separate for focused tests. */

import type { CoveIndex } from "./types";

/** One directed, aggregated set of page references between two coves. */
export interface CoveConnection {
  source: string;
  target: string;
  referenceCount: number;
  sourcePageCount: number;
}

/** Aggregate page-level wiki references into directed cove-to-cove links. */
export function coveConnections(coves: CoveIndex[]): CoveConnection[] {
  const aliases = new Set(coves.map((cove) => cove.alias));
  const connections = new Map<
    string,
    { source: string; target: string; references: number; pages: Set<string> }
  >();

  for (const cove of coves) {
    for (const page of cove.pages) {
      for (const reference of page.references) {
        if (reference.cove === cove.alias || !aliases.has(reference.cove)) continue;
        const key = `${cove.alias}\u0000${reference.cove}`;
        let connection = connections.get(key);
        if (!connection) {
          connection = {
            source: cove.alias,
            target: reference.cove,
            references: 0,
            pages: new Set(),
          };
          connections.set(key, connection);
        }
        connection.references += 1;
        connection.pages.add(page.path);
      }
    }
  }

  return [...connections.values()]
    .map((connection) => ({
      source: connection.source,
      target: connection.target,
      referenceCount: connection.references,
      sourcePageCount: connection.pages.size,
    }))
    .sort(
      (left, right) =>
        left.source.localeCompare(right.source) || left.target.localeCompare(right.target),
    );
}
