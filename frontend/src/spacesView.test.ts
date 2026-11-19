/**
 * Tests the Spaces view preference round-trip against jsdom's real
 * `window.localStorage` (installed by `bunfig.toml`'s `[test] preload`,
 * see `testSetup.ts`) — junk and absent values must fall back to "list".
 */

import { beforeEach, expect, test } from "bun:test";

import { getSpacesView, setSpacesView } from "./spacesView";

beforeEach(() => {
  window.localStorage.clear();
});

test("defaults to list", () => {
  expect(getSpacesView()).toBe("list");
});

test("round-trips grid", () => {
  setSpacesView("grid");
  expect(getSpacesView()).toBe("grid");
});

test("round-trips graph", () => {
  setSpacesView("graph");
  expect(getSpacesView()).toBe("graph");
});

test("round-trips back to list", () => {
  setSpacesView("grid");
  setSpacesView("list");
  expect(getSpacesView()).toBe("list");
});

test("ignores junk stored values", () => {
  window.localStorage.setItem("reef.spacesView", "carousel");
  expect(getSpacesView()).toBe("list");
});
