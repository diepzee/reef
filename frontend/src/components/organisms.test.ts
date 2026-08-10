import { describe, expect, test } from "bun:test";

import { FAMILIES, fnv1a, generateFamily, mulberry32 } from "./organisms";

/** Which way each family anchors in the 64-box. */
const EXPECTED_ANCHOR: Record<string, string> = {
  sunAnemone: "radial",
  flower: "radial",
  spiral: "radial",
  tubes: "grounded",
  seagrass: "grounded",
  bubbles: "grounded",
  staghorn: "grounded",
  brain: "grounded",
  scallop: "grounded",
  shell: "grounded",
  nudibranch: "grounded",
};

/** The families implemented so far — grows task by task until it equals FAMILIES. */
const IMPLEMENTED = [
  "sunAnemone",
  "flower",
  "spiral",
  "tubes",
  "seagrass",
  "bubbles",
  "staghorn",
] as const;

describe("generateFamily", () => {
  test("families are deterministic and emit valid paths", () => {
    for (const fam of IMPLEMENTED) {
      for (let seed = 1; seed <= 50; seed++) {
        const a = generateFamily(fam, seed);
        const b = generateFamily(fam, seed);
        expect(a).toEqual(b);
        expect(a.anchor).toEqual(EXPECTED_ANCHOR[fam] as never);
        expect(a.paths.length).toBeGreaterThan(0);
        for (const p of a.paths) {
          expect(p.d.startsWith("M")).toBeTrue();
          expect(p.d).not.toContain("NaN");
          expect(p.d).not.toContain("undefined");
        }
      }
    }
  });
});

describe("fnv1a", () => {
  test("is deterministic and 32-bit unsigned", () => {
    expect(fnv1a("roadtrip")).toEqual(fnv1a("roadtrip"));
    expect(fnv1a("roadtrip")).toBeGreaterThanOrEqual(0);
    expect(fnv1a("roadtrip")).toBeLessThanOrEqual(0xffffffff);
  });

  test("separates anagrams (unlike char-sum)", () => {
    expect(fnv1a("stop")).not.toEqual(fnv1a("pots"));
  });
});

describe("mulberry32", () => {
  test("same seed → same sequence, in [0,1)", () => {
    const a = mulberry32(42);
    const b = mulberry32(42);
    for (let i = 0; i < 100; i++) {
      const v = a();
      expect(v).toEqual(b());
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });

  test("different seeds diverge", () => {
    expect(mulberry32(1)()).not.toEqual(mulberry32(2)());
  });
});
