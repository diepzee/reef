import { describe, expect, test } from "bun:test";

import { fnv1a, mulberry32 } from "./organisms";

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
