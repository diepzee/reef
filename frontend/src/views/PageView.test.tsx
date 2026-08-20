/**
 * Reading a page: what a protected page withholds, and the guard in front
 * of deleting one.
 *
 * Two things here are destructive if they slip. A `meta/` page must never
 * offer an Edit link or a Delete button — those pages update only through
 * their own tool, and the protocol is what the assistant runs on. And
 * deleting takes a page and its whole history permanently, so it has to sit
 * behind a confirm rather than a single stray click.
 */

import { createContext } from "react";
import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AppearanceContext } from "../useAppearance";

class FakeApiError extends Error {
  status: number;
  code: string;
  detail?: string;
  constructor(status: number, code: string, detail?: string) {
    super(detail ? `${code}: ${detail}` : code);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

let sent: Array<{ method: string; path: string }> = [];
let page: Record<string, unknown> = {};
let deleteResponse: () => unknown = () => ({});
/** Set to an error to make the page load fail. */
let loadFails: Error | null = null;
let navigated: string[] = [];

// Every mock of `../api` must present its whole surface. `mock.module`
// replaces the module for the entire `bun test` run, so a partial mock here
// becomes the module some *other* view imports, and it fails at import time
// with "Export named 'apiDownload' not found" — a long way from the file
// that caused it. Unused entries below are deliberate.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiGet: () => (loadFails ? Promise.reject(loadFails) : Promise.resolve(page)),
  apiSend: (method: string, path: string) => {
    sent.push({ method, path });
    return Promise.resolve(deleteResponse());
  },
  apiDownload: () => Promise.resolve(),
}));

mock.module("../IndexProvider", () => ({
  useIndex: () => ({ refresh: () => Promise.resolve() }),
}));
mock.module("../useMembers", () => ({ useMembers: () => ({ members: null }) }));
// Both exports: AppShell imports the context to provide it, and a mock
// missing it breaks that file at import time (see NewPage.test.tsx).
mock.module("../useMembersSheet", () => ({
  MembersSheetContext: createContext<unknown>(null),
  useMembersSheet: () => ({ openMembers: () => {} }),
}));
mock.module("react-router-dom", () => ({
  ...require("react-router-dom"),
  useNavigate: () => (to: string) => navigated.push(to),
}));

const { default: PageView } = await import("./PageView");

/** Render the view for a page at `path` in cove `cove`. */
async function renderPage(path: string, cove = "trip") {
  render(
    <AppearanceContext.Provider
      value={{ appearance: {}, setAppearance: () => {} }}
    >
      <MemoryRouter initialEntries={[`/s/${cove}/p/${path}`]}>
      <Routes>
        <Route path="/s/:cove/p/*" element={<PageView />} />
      </Routes>
      </MemoryRouter>
    </AppearanceContext.Provider>,
  );
  await waitFor(() => expect(screen.queryByText("Loading…")).toBeNull());
}

beforeEach(() => {
  sent = [];
  navigated = [];
  deleteResponse = () => ({});
  loadFails = null;
  page = {
    path: "packing.md",
    title: "Packing",
    body: "# Packing\n\nwoollens",
    tags: ["gear"],
    version: 3,
    updated: "2026-08-13T10:00:00Z",
    last_editor: "Wouter",
  };
});

afterEach(cleanup);

test("renders the page's markdown as HTML", async () => {
  await renderPage("packing.md");
  expect(screen.getByText("woollens")).toBeDefined();
  expect(screen.getByText("gear")).toBeDefined();
});

test("an ordinary page can be edited and deleted", async () => {
  await renderPage("packing.md");
  expect(screen.getByText("Edit").getAttribute("href")).toBe(
    "/s/trip/e/packing.md",
  );
  expect(screen.getByText("Delete this page…")).toBeDefined();
});

test("a meta page offers neither editing nor deletion", async () => {
  // The protocol page is what the assistant runs on; the web editor must
  // not be a second way to rewrite it.
  page = { ...page, path: "meta/protocol.md", title: "Protocol" };
  await renderPage("meta/protocol.md");
  expect(screen.queryByText("Edit")).toBeNull();
  expect(screen.queryByText("Delete this page…")).toBeNull();
  expect(screen.getByText(/Protected page/)).toBeDefined();
});

test("deleting asks first, and says what it takes with it", async () => {
  await renderPage("packing.md");
  fireEvent.click(screen.getByText("Delete this page…"));
  // Nothing has been sent yet -- the first click only opens the confirm.
  expect(sent).toEqual([]);
  expect(screen.getByText(/its whole history, permanently/)).toBeDefined();
});

test("cancelling a delete leaves the page alone", async () => {
  await renderPage("packing.md");
  fireEvent.click(screen.getByText("Delete this page…"));
  fireEvent.click(screen.getByText("Cancel"));
  expect(sent).toEqual([]);
  expect(screen.getByText("Delete this page…")).toBeDefined();
});

test("confirming deletes it and returns to the cove", async () => {
  await renderPage("packing.md");
  fireEvent.click(screen.getByText("Delete this page…"));
  fireEvent.click(screen.getByText("Delete permanently"));
  await waitFor(() => expect(navigated).toEqual(["/s/trip"]));
  expect(sent).toEqual([
    { method: "DELETE", path: "/api/pages/trip/packing.md" },
  ]);
});

test("a refused delete says why and leaves the page in place", async () => {
  deleteResponse = () => {
    throw new FakeApiError(403, "protected", "that page is protected");
  };
  await renderPage("packing.md");
  fireEvent.click(screen.getByText("Delete this page…"));
  fireEvent.click(screen.getByText("Delete permanently"));
  await waitFor(() =>
    expect(screen.getByText("that page is protected")).toBeDefined(),
  );
  expect(navigated).toEqual([]);
});

test("a page that will not load says so instead of hanging", async () => {
  loadFails = new FakeApiError(404, "not_found");
  render(
    <AppearanceContext.Provider
      value={{ appearance: {}, setAppearance: () => {} }}
    >
      <MemoryRouter initialEntries={["/s/trip/p/gone.md"]}>
      <Routes>
        <Route path="/s/:cove/p/*" element={<PageView />} />
      </Routes>
      </MemoryRouter>
    </AppearanceContext.Provider>,
  );
  await waitFor(() => expect(screen.getByText("not_found")).toBeDefined());
  // Not stuck on the spinner, and offering no actions for a page that
  // isn't there.
  expect(screen.queryByText("Loading…")).toBeNull();
  expect(screen.queryByText("Delete this page…")).toBeNull();
});
