/**
 * A cove's own page, and the two different ways out of it.
 *
 * Which exit is offered depends on whether anyone else is here, and the two
 * are not interchangeable: leaving is survivable, deleting takes the cove's
 * pages, files, and history permanently. Offering the wrong one — or
 * describing it wrongly — is how somebody destroys a shared cove believing
 * they were stepping out of it. The wording is part of the contract, so it
 * is asserted alongside the requests.
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

let sent: Array<{ method: string; path: string; body?: unknown }> = [];
let response: () => unknown = () => ({});
let navigated: string[] = [];
let members: { members: Array<{ display_name: string }>; is_owner: boolean } | null;

// Every mock of `../api` must present its whole surface. `mock.module`
// replaces the module for the entire `bun test` run, so a partial mock here
// becomes the module some *other* view imports, and it fails at import time
// with "Export named 'apiDownload' not found" — a long way from the file
// that caused it. Unused entries below are deliberate.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve(response());
  },
  apiGet: () => Promise.resolve({}),
  apiDownload: () => Promise.resolve(),
}));

mock.module("../IndexProvider", () => ({
  useIndex: () => ({
    // "personal" too: the page list, and the appearance section under it,
    // only render for a cove the index actually holds.
    index: {
      coves: [
        { alias: "trip", pages: [] },
        { alias: "personal", pages: [] },
      ],
    },
    error: null,
    refresh: () => Promise.resolve(),
  }),
}));
mock.module("../useMembers", () => ({
  useMembers: () => ({ members, error: null }),
}));
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

const { default: CoveView } = await import("./CoveView");

/** Render the cove page for `cove`. */
function renderCove(cove = "trip") {
  render(
    <AppearanceContext.Provider
      value={{ appearance: {}, setAppearance: () => {} }}
    >
      <MemoryRouter initialEntries={[`/s/${cove}`]}>
      <Routes>
        <Route path="/s/:cove" element={<CoveView />} />
      </Routes>
      </MemoryRouter>
    </AppearanceContext.Provider>,
  );
}

/** A roster of `n` people, owned by this person unless said otherwise. */
function roster(n: number, is_owner = true) {
  return {
    members: Array.from({ length: n }, (_, i) => ({ display_name: `P${i}` })),
    is_owner,
  };
}

beforeEach(() => {
  sent = [];
  navigated = [];
  response = () => ({});
  members = roster(3);
});

afterEach(cleanup);

test("no cove is left or deleted from this screen", () => {
  // Leaving and deleting moved into the members sheet's danger zone — see
  // MembersSheet.test.tsx, which holds the guards. What matters here is that
  // a permanent delete no longer sits one scroll under the page list.
  renderCove();
  expect(screen.queryByText(/Leave this cove/)).toBeNull();
  expect(screen.queryByText(/Delete this cove/)).toBeNull();
});

test("the personal cove is styled from this screen, having no sheet", () => {
  // Every shared cove reaches the picker from Manage. The personal one has
  // no roster and so no sheet, and would otherwise lose the control.
  renderCove("personal");
  expect(screen.getByText("Appearance")).toBeDefined();
  expect(screen.getByText("Colour")).toBeDefined();
});

test("a shared cove keeps its appearance behind Manage, not in the body", () => {
  renderCove();
  expect(screen.queryByText("Appearance")).toBeNull();
});
