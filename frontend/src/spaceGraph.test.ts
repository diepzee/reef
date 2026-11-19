import { expect, test } from "bun:test";

import { spaceConnections } from "./spaceGraph";
import type { SpaceIndex } from "./types";

function space(
  alias: string,
  engrams: Array<{ path: string; references: Array<{ space: string; path: string }> }>,
): SpaceIndex {
  return {
    alias,
    version: 1,
    attachments: [],
    engrams: engrams.map((engram) => ({
      ...engram,
      title: engram.path,
      tags: [],
      description: "",
      updated: "2026-08-11T00:00:00",
      size: 1,
      version: 1,
      last_editor: null,
    })),
  };
}

test("aggregates cross-space references by direction and source engram", () => {
  const graph = spaceConnections([
    space("personal", [
      {
        path: "a.md",
        references: [
          { space: "personal", path: "b.md" },
          { space: "household", path: "home.md" },
          { space: "household", path: "chores.md" },
        ],
      },
      {
        path: "b.md",
        references: [{ space: "household", path: "home.md" }],
      },
    ]),
    space("household", [
      {
        path: "home.md",
        references: [{ space: "personal", path: "a.md" }],
      },
    ]),
  ]);

  expect(graph).toEqual([
    {
      source: "household",
      target: "personal",
      referenceCount: 1,
      sourceEngramCount: 1,
    },
    {
      source: "personal",
      target: "household",
      referenceCount: 3,
      sourceEngramCount: 2,
    },
  ]);
});

test("ignores references to a space outside the supplied index", () => {
  expect(
    spaceConnections([
      space("personal", [
        { path: "a.md", references: [{ space: "hidden", path: "x.md" }] },
      ]),
    ]),
  ).toEqual([]);
});
