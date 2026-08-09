import { expect, test } from "bun:test";
import { spaceColor } from "./spaceColor";

const ALL_PAIRS = [
  { base: "#0d9488", light: "#5eead4" }, // personal (seafoam)
  { base: "#f59e0b", light: "#fbbf24" },
  { base: "#6366f1", light: "#a5b4fc" },
  { base: "#ec4899", light: "#f9a8d4" },
  { base: "#0284c7", light: "#7dd3fc" },
  { base: "#84cc16", light: "#bef264" },
  { base: "#8b5cf6", light: "#c4b5fd" },
  { base: "#f97316", light: "#fdba74" },
];

test("deterministic per alias", () => {
  expect(spaceColor("roadtrip")).toEqual(spaceColor("roadtrip"));
});

test("personal is always fixed seafoam", () => {
  expect(spaceColor("personal")).toEqual({ base: "#0d9488", light: "#5eead4" });
});

test("result is always one of the eight pairs", () => {
  for (const alias of ["personal", "roadtrip", "kitchen", "張三", "", "a", "zzz"]) {
    expect(ALL_PAIRS).toContainEqual(spaceColor(alias));
  }
});
