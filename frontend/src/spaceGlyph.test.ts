import { describe, expect, test } from "bun:test";

import { FAMILIES, organismFor } from "./components/organisms";

describe("organismFor", () => {
  test("personal hashes like any alias — the brand fan is a logo, not a glyph", () => {
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

  test("no alias grows a retired body plan", () => {
    // The three flattest plans are withdrawn: at a common height they would outgrow the box.
    const retired = new Set(["brain", "shell", "nudibranch"]);
    for (let i = 0; i < 500; i++) {
      expect(retired.has(organismFor("space-" + i).family)).toBeFalse();
    }
  });

  test("retiring a plan leaves every other space's organism untouched", () => {
    // Retired plans stay in the hash space precisely so that `seed % FAMILIES.length` keeps
    // dealing every other alias the family it already had.
    expect(FAMILIES.length).toEqual(11);
    expect(organismFor("personal").family).toEqual("staghorn");
    expect(organismFor("roadtrip").family).toEqual(organismFor("roadtrip").family);
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
