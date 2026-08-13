/**
 * The whole-reef index: every cove, its pages, and how they link.
 *
 * The paths are the load-bearing part. A page path can contain anything a
 * title can, so a link built without encoding sends the reader somewhere
 * else entirely — and an index is exactly where that would go unnoticed,
 * because nobody reads every row.
 */

import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AppearanceContext } from "../useAppearance";

let index: unknown = null;
let error: string | null = null;

mock.module("../IndexProvider", () => ({
  useIndex: () => ({ rawIndex: index, error, refresh: () => Promise.resolve() }),
}));

const { default: IndexView } = await import("./IndexView");

function renderIndex() {
  render(
    // Each cove wears its own creature here now, and that is read from the
    // viewer's appearance choices — so the view needs the provider.
    <AppearanceContext.Provider
      value={{ appearance: {} as never, setAppearance: () => {} }}
    >
      <MemoryRouter>
        <IndexView />
      </MemoryRouter>
    </AppearanceContext.Provider>,
  );
}

beforeEach(() => {
  error = null;
  index = {
    spaces: [
      {
        alias: "trip",
        version: 4,
        pages: [
          {
            path: "packing.md",
            title: "Packing",
            tags: ["gear"],
            references: [],
            size: 1234,
            updated: "2026-08-13T10:00:00Z",
          },
        ],
        attachments: [],
      },
    ],
  };
});

afterEach(cleanup);

test("each cove links to itself and lists its pages", () => {
  renderIndex();
  expect(screen.getByText("trip").getAttribute("href")).toBe("/s/trip");
  expect(screen.getByText("Packing").getAttribute("href")).toBe(
    "/s/trip/p/packing.md",
  );
});

test("a cove with nothing in it says so rather than looking broken", () => {
  index = { spaces: [{ alias: "trip", pages: [], attachments: [] }] };
  renderIndex();
  expect(screen.getByText("No pages.")).toBeDefined();
});

test("tags are shown against their page", () => {
  renderIndex();
  expect(screen.getByText("gear")).toBeDefined();
});

test("an index that failed to load reports it", () => {
  index = null;
  error = "could not load the index";
  renderIndex();
  expect(screen.getByText("could not load the index")).toBeDefined();
});

test("it offers the way back", () => {
  renderIndex();
  expect(document.querySelector(".index-back")).not.toBeNull();
});
