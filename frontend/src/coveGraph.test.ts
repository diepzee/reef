import { expect, test } from "bun:test";

import { coveConnections } from "./coveGraph";
import type { SpaceIndex } from "./types";

function space(
  alias: string,
  pages: Array<{ path: string; references: Array<{ space: string; path: string }> }>,
): SpaceIndex {
  return {
    alias,
    version: 1,
    attachments: [],
    pages: pages.map((page) => ({
      ...page,
      title: page.path,
      tags: [],
      description: "",
      updated: "2026-08-11T00:00:00",
      size: 1,
      version: 1,
      last_editor: null,
    })),
  };
}

test("aggregates cross-cove references by direction and source page", () => {
  const graph = coveConnections([
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
      sourcePageCount: 1,
    },
    {
      source: "personal",
      target: "household",
      referenceCount: 3,
      sourcePageCount: 2,
    },
  ]);
});

test("ignores references to a cove outside the supplied index", () => {
  expect(
    coveConnections([
      space("personal", [
        { path: "a.md", references: [{ space: "hidden", path: "x.md" }] },
      ]),
    ]),
  ).toEqual([]);
});
