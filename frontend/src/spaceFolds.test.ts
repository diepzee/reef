/**
 * Tests the sidebar's fold memory against jsdom's real `window.localStorage`
 * (installed by `bunfig.toml`'s `[test] preload`, see `testSetup.ts`).
 *
 * The case that matters most is the difference between "never said" and
 * "said shut": the active space opens by default, but only until the reader
 * overrules it, or closing the space you are standing in would not stick.
 */

import { beforeEach, expect, test } from "bun:test";

import { getSpaceFolds, isSpaceOpen, setSpaceFold } from "./spaceFolds";

const KEY = "reef.sidebar.openSpaces";

beforeEach(() => {
  window.localStorage.clear();
});

test("starts with no recorded folds", () => {
  expect(getSpaceFolds()).toEqual({});
});

test("round-trips a fold", () => {
  const folds = setSpaceFold({}, "household", false);
  expect(folds).toEqual({ household: false });
  expect(getSpaceFolds()).toEqual({ household: false });
});

test("recording one space leaves the others alone", () => {
  let folds = setSpaceFold({}, "household", true);
  folds = setSpaceFold(folds, "personal", false);
  expect(getSpaceFolds()).toEqual({ household: true, personal: false });
});

test("an untouched space opens only when it is the active one", () => {
  expect(isSpaceOpen({}, "household", true, true)).toBe(true);
  expect(isSpaceOpen({}, "household", false, true)).toBe(false);
});

test("closing the space you are standing in sticks", () => {
  // The regression this guards: falling back to `isActive` whenever the map
  // has no entry would re-open it on the very next render.
  const folds = setSpaceFold({}, "household", false);
  expect(isSpaceOpen(folds, "household", true, true)).toBe(false);
});

test("opening a space you are not in sticks", () => {
  const folds = setSpaceFold({}, "personal", true);
  expect(isSpaceOpen(folds, "personal", false, true)).toBe(true);
});

test("a space with no engrams never opens", () => {
  expect(isSpaceOpen({ test: true }, "test", true, false)).toBe(false);
});

test("ignores a corrupt stored value", () => {
  window.localStorage.setItem(KEY, "{not json");
  expect(getSpaceFolds()).toEqual({});
});

test("ignores a stored array", () => {
  // Would otherwise come back as {"0": ...}, an alias no space has.
  window.localStorage.setItem(KEY, JSON.stringify(["household"]));
  expect(getSpaceFolds()).toEqual({});
});

test("drops non-boolean entries but keeps the rest", () => {
  window.localStorage.setItem(KEY, JSON.stringify({ household: "yes", personal: true }));
  expect(getSpaceFolds()).toEqual({ personal: true });
});
