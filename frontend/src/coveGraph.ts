/** Pure graph projection from the index, kept separate for focused tests. */

import type { SpaceIndex } from "./types";

/** One directed, aggregated set of page references between two coves. */
export interface CoveConnection {
  source: string;
  target: string;
  referenceCount: number;
  sourcePageCount: number;
}

/** Aggregate page-level wiki references into directed cove-to-cove links. */
export function coveConnections(spaces: SpaceIndex[]): CoveConnection[] {
  const aliases = new Set(spaces.map((space) => space.alias));
  const connections = new Map<
    string,
    { source: string; target: string; references: number; pages: Set<string> }
  >();

  for (const space of spaces) {
    for (const page of space.pages) {
      for (const reference of page.references) {
        if (reference.space === space.alias || !aliases.has(reference.space)) continue;
        const key = `${space.alias}\u0000${reference.space}`;
        let connection = connections.get(key);
        if (!connection) {
          connection = {
            source: space.alias,
            target: reference.space,
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
