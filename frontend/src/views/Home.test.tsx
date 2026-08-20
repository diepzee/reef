/**
 * The coves list, and the view choice that persists across visits.
 *
 * `covesView.test.ts` covers reading and writing the stored preference.
 * What only rendering shows is that the picker is a real tablist — the
 * selected tab has to report itself, or the choice is invisible to anyone
 * not looking at the highlight — and that choosing one records it.
 */

import { createContext } from "react";
import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AppearanceContext } from "../useAppearance";

let index: unknown = null;
let error: string | null = null;

mock.module("../IndexProvider", () => ({
  useIndex: () => ({ index, error, refresh: () => Promise.resolve() }),
}));
mock.module("../useMembers", () => ({
  useMembers: () => ({ members: null, error: null }),
}));
// Both exports: AppShell imports the context to provide it, and a mock
// missing it breaks that file at import time (see NewPage.test.tsx).
mock.module("../useMembersSheet", () => ({
  MembersSheetContext: createContext<unknown>(null),
  useMembersSheet: () => ({ openMembers: () => {} }),
}));

const { default: Home } = await import("./Home");

function renderHome() {
  render(
    <AppearanceContext.Provider
      value={{ appearance: {} as never, setAppearance: () => {} }}
    >
      <MemoryRouter>
        <Home />
      </MemoryRouter>
    </AppearanceContext.Provider>,
  );
}

/** One cove's index row. */
function cove(alias: string) {
  return { alias, version: 1, pages: [], attachments: [] };
}

beforeEach(() => {
  error = null;
  window.localStorage.clear();
  index = { coves: [cove("trip"), cove("home")] };
});

afterEach(cleanup);

test("every cove is listed", () => {
  renderHome();
  expect(screen.getByText("trip")).toBeDefined();
  expect(screen.getByText("home")).toBeDefined();
});

test("the shortcuts to the index and export are offered", () => {
  renderHome();
  expect(screen.getByText("Index").getAttribute("href")).toBe("/index");
  expect(screen.getByText("Export").getAttribute("href")).toBe("/export");
});

test("the view picker reports which view is selected", () => {
  // A highlight alone leaves the state invisible to a screen reader.
  renderHome();
  const list = screen.getByLabelText("List view");
  const tiles = screen.getByLabelText("Tile view");
  fireEvent.click(list);
  expect(list.getAttribute("aria-selected")).toBe("true");
  expect(tiles.getAttribute("aria-selected")).toBe("false");
});

test("choosing a view records it for next time", () => {
  renderHome();
  fireEvent.click(screen.getByLabelText("Tile view"));
  expect(screen.getByLabelText("Tile view").getAttribute("aria-selected")).toBe(
    "true",
  );
  // Persisted, so the next visit opens the way this one was left. The
  // stored value is "grid" though the control reads "Tile view" -- the
  // label and the stored token are not the same vocabulary.
  expect(window.localStorage.getItem("reef.covesView")).toBe("grid");
});

test("an index that failed to load reports it", () => {
  index = null;
  error = "could not load your coves";
  renderHome();
  expect(screen.getByText("could not load your coves")).toBeDefined();
});
