/**
 * The organism assignment must be deterministic, personal-pinned, and
 * always land inside the six-organism family — mirroring `spaceColor`'s
 * contract (personal pinned to seafoam, everything else hashed).
 */

import { expect, test } from "bun:test";

import { ORGANISMS, organismFor } from "./components/spaceGlyph";

test("personal is always the brand coral", () => {
  expect(organismFor("personal")).toBe("coral");
});

test("assignment is deterministic", () => {
  expect(organismFor("school")).toBe(organismFor("school"));
});

test("non-personal aliases stay inside the organism family", () => {
  for (const alias of ["school", "roadtrip", "recipes", "thesis", "garden", "crew"]) {
    expect(ORGANISMS).toContain(organismFor(alias));
  }
});

test("different aliases can land on different organisms", () => {
  const kinds = new Set(
    ["school", "roadtrip", "recipes", "thesis", "garden", "crew"].map(organismFor),
  );
  expect(kinds.size).toBeGreaterThan(1);
});
