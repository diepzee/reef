/**
 * Tests the sidebar's fold memory against jsdom's real `window.localStorage`
 * (installed by `bunfig.toml`'s `[test] preload`, see `testSetup.ts`).
 *
 * The case that matters most is the difference between "never said" and
 * "said shut": the active cove opens by default, but only until the reader
 * overrules it, or closing the cove you are standing in would not stick.
 */

import { beforeEach, expect, test } from "bun:test";

import { getCoveFolds, isCoveOpen, setCoveFold } from "./coveFolds";

const KEY = "reef.sidebar.openCoves";

beforeEach(() => {
  window.localStorage.clear();
});

test("starts with no recorded folds", () => {
  expect(getCoveFolds()).toEqual({});
});

test("round-trips a fold", () => {
  const folds = setCoveFold({}, "household", false);
  expect(folds).toEqual({ household: false });
  expect(getCoveFolds()).toEqual({ household: false });
});

test("recording one cove leaves the others alone", () => {
  let folds = setCoveFold({}, "household", true);
  folds = setCoveFold(folds, "personal", false);
  expect(getCoveFolds()).toEqual({ household: true, personal: false });
});

test("an untouched cove opens only when it is the active one", () => {
  expect(isCoveOpen({}, "household", true, true)).toBe(true);
  expect(isCoveOpen({}, "household", false, true)).toBe(false);
});

test("closing the cove you are standing in sticks", () => {
  // The regression this guards: falling back to `isActive` whenever the map
  // has no entry would re-open it on the very next render.
  const folds = setCoveFold({}, "household", false);
  expect(isCoveOpen(folds, "household", true, true)).toBe(false);
});

test("opening a cove you are not in sticks", () => {
  const folds = setCoveFold({}, "personal", true);
  expect(isCoveOpen(folds, "personal", false, true)).toBe(true);
});

test("a cove with no pages never opens", () => {
  expect(isCoveOpen({ test: true }, "test", true, false)).toBe(false);
});

test("ignores a corrupt stored value", () => {
  window.localStorage.setItem(KEY, "{not json");
  expect(getCoveFolds()).toEqual({});
});

test("ignores a stored array", () => {
  // Would otherwise come back as {"0": ...}, an alias no cove has.
  window.localStorage.setItem(KEY, JSON.stringify(["household"]));
  expect(getCoveFolds()).toEqual({});
});

test("drops non-boolean entries but keeps the rest", () => {
  window.localStorage.setItem(KEY, JSON.stringify({ household: "yes", personal: true }));
  expect(getCoveFolds()).toEqual({ personal: true });
});
