import { expect, test } from "bun:test";

import { coveConnections } from "./coveGraph";
import type { CoveIndex } from "./types";

function cove(
  alias: string,
  pages: Array<{ path: string; references: Array<{ cove: string; path: string }> }>,
): CoveIndex {
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
    cove("personal", [
      {
        path: "a.md",
        references: [
          { cove: "personal", path: "b.md" },
          { cove: "household", path: "home.md" },
          { cove: "household", path: "chores.md" },
        ],
      },
      {
        path: "b.md",
        references: [{ cove: "household", path: "home.md" }],
      },
    ]),
    cove("household", [
      {
        path: "home.md",
        references: [{ cove: "personal", path: "a.md" }],
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
      cove("personal", [
        { path: "a.md", references: [{ cove: "hidden", path: "x.md" }] },
      ]),
    ]),
  ).toEqual([]);
});
