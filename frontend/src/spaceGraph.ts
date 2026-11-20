/** Pure graph projection from the index, kept separate for focused tests. */

import type { SpaceIndex } from "./types";

/** One directed, aggregated set of engram references between two spaces. */
export interface SpaceConnection {
  source: string;
  target: string;
  referenceCount: number;
  sourceEngramCount: number;
}

/** Aggregate engram-level wiki references into directed space-to-space links. */
export function spaceConnections(spaces: SpaceIndex[]): SpaceConnection[] {
  const aliases = new Set(spaces.map((space) => space.alias));
  const connections = new Map<
    string,
    { source: string; target: string; references: number; engrams: Set<string> }
  >();

  for (const space of spaces) {
    for (const engram of space.engrams) {
      for (const reference of engram.references) {
        if (reference.space === space.alias || !aliases.has(reference.space)) continue;
        const key = `${space.alias}\u0000${reference.space}`;
        let connection = connections.get(key);
        if (!connection) {
          connection = {
            source: space.alias,
            target: reference.space,
            references: 0,
            engrams: new Set(),
          };
          connections.set(key, connection);
        }
        connection.references += 1;
        connection.engrams.add(engram.path);
      }
    }
  }

  return [...connections.values()]
    .map((connection) => ({
      source: connection.source,
      target: connection.target,
      referenceCount: connection.references,
      sourceEngramCount: connection.engrams.size,
    }))
    .sort(
      (left, right) =>
        left.source.localeCompare(right.source) || left.target.localeCompare(right.target),
    );
}
