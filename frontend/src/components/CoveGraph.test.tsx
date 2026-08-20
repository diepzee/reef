/**
 * The cove graph: a picture of the whole reef.
 *
 * A drawing that carries information has to describe itself, or it is
 * simply absent for anyone not looking at it. `organisms.test.ts` and
 * `coveColor.test.ts` already cover the geometry and hues underneath.
 */

import { afterEach, expect, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AppearanceContext } from "../useAppearance";
import { CoveGraph } from "./CoveGraph";

/** Render the graph for `coves`, under the contexts it expects. */
function renderGraph(coves: unknown[]) {
  return render(
    <AppearanceContext.Provider
      value={{ appearance: {} as never, setAppearance: () => {} }}
    >
      <MemoryRouter>
        <CoveGraph coves={coves as never} />
      </MemoryRouter>
    </AppearanceContext.Provider>,
  );
}

/** One cove's index row, with `pages` pages in it. */
function cove(alias: string, pages = 2) {
  return {
    alias,
    version: 1,
    pages: Array.from({ length: pages }, (_, i) => ({
      path: `p${i}.md`,
      title: `P${i}`,
      tags: [],
      references: [],
      size: 10,
      updated: "2026-08-13T10:00:00Z",
    })),
    attachments: [],
  };
}

afterEach(cleanup);

test("the drawing describes itself rather than being a silent picture", () => {
  renderGraph([cove("trip"), cove("home")]);
  expect(screen.getByRole("img")).toBeDefined();
  // aria-labelledby points at a title and description that actually exist.
  const svg = screen.getByRole("img");
  for (const id of (svg.getAttribute("aria-labelledby") ?? "").split(/\s+/)) {
    expect(document.getElementById(id)).not.toBeNull();
  }
});

test("an empty reef still renders instead of throwing", () => {
  // A brand-new account reaches this before it has made anything.
  const { container } = renderGraph([]);
  expect(container.querySelector("svg")).not.toBeNull();
});

test("a cove with no pages is drawn too", () => {
  const { container } = renderGraph([cove("trip", 0)]);
  expect(container.querySelector("svg")).not.toBeNull();
});
