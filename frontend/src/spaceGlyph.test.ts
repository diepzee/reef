import { describe, expect, test } from "bun:test";

import { FAMILIES, organismFor } from "./components/organisms";

describe("organismFor", () => {
  test("personal is always the fan coral", () => {
    expect(organismFor("personal").family).toEqual("coral");
  });

  test("deterministic per alias", () => {
    expect(organismFor("roadtrip")).toEqual(organismFor("roadtrip"));
  });

  test("non-personal aliases stay inside the hashed families", () => {
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
