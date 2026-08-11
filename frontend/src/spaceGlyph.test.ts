import { describe, expect, test } from "bun:test";

import { FAMILIES, organismFor } from "./components/organisms";

describe("organismFor", () => {
  test("personal hashes like any alias — the brand coral is a logo, not a glyph", () => {
    expect(organismFor("personal").family).toEqual("staghorn");
    expect(FAMILIES).toContain(organismFor("personal").family as never);
  });

  test("deterministic per alias", () => {
    expect(organismFor("roadtrip")).toEqual(organismFor("roadtrip"));
  });

  test("every alias stays inside the hashed families", () => {
    for (const alias of ["roadtrip", "household", "boekenclub", "diepzee", "atelier"]) {
      expect(FAMILIES).toContain(organismFor(alias).family as never);
    }
  });

  test("a realistic corpus reaches many families", () => {
    const corpus = [
      "roadtrip", "household", "boekenclub", "diepzee", "atelier", "garden",
      "budget", "recipes", "wedding", "band", "chess", "surf", "lab", "crew",
      "tribe", "nest",
    ];
    const seen = new Set(corpus.map((a) => organismFor(a).family));
    expect(seen.size).toBeGreaterThanOrEqual(7);
  });
});
