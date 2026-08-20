/**
 * Tests the Coves view preference round-trip against jsdom's real
 * `window.localStorage` (installed by `bunfig.toml`'s `[test] preload`,
 * see `testSetup.ts`) — junk and absent values must fall back to "list".
 */

import { beforeEach, expect, test } from "bun:test";

import { getCovesView, setCovesView } from "./covesView";

beforeEach(() => {
  window.localStorage.clear();
});

test("defaults to list", () => {
  expect(getCovesView()).toBe("list");
});

test("round-trips grid", () => {
  setCovesView("grid");
  expect(getCovesView()).toBe("grid");
});

test("round-trips graph", () => {
  setCovesView("graph");
  expect(getCovesView()).toBe("graph");
});

test("round-trips back to list", () => {
  setCovesView("grid");
  setCovesView("list");
  expect(getCovesView()).toBe("list");
});

test("ignores junk stored values", () => {
  window.localStorage.setItem("reef.covesView", "carousel");
  expect(getCovesView()).toBe("list");
});
