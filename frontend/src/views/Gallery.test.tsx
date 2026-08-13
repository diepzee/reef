/**
 * The organism gallery — the dev-only tuning surface for the generators.
 *
 * It is a development tool, not a product screen, so what matters is only
 * that it still renders every family after the generators change. Anything
 * finer belongs in `organisms.test.ts`, which owns the geometry.
 */

import { afterEach, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";

import { FAMILIES } from "../components/organisms";
import { Gallery } from "./Gallery";

afterEach(cleanup);

test("every family gets a section", () => {
  const { container } = render(<Gallery />);
  const headings = [...container.querySelectorAll("h2")].map(
    (h) => h.textContent,
  );
  for (const family of FAMILIES) expect(headings).toContain(family);
});

test("each family is drawn across the seed sweep", () => {
  // The sweep is the whole point: one seed proves nothing about the range.
  const { container } = render(<Gallery />);
  expect(container.querySelectorAll("svg").length).toBeGreaterThan(
    FAMILIES.length,
  );
});
