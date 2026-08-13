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
    index: { spaces: [{ alias: "trip", pages: [] }] },
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

const { default: SpaceView } = await import("./SpaceView");

/** Render the cove page for `space`. */
function renderSpace(space = "trip") {
  render(
    <AppearanceContext.Provider
      value={{ appearance: {}, setAppearance: () => {} }}
    >
      <MemoryRouter initialEntries={[`/s/${space}`]}>
      <Routes>
        <Route path="/s/:space" element={<SpaceView />} />
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

test("with others here, the exit is leaving — never deleting", () => {
  renderSpace();
  expect(screen.getByText("Leave this cove")).toBeDefined();
  expect(screen.queryByText("Delete this cove")).toBeNull();
});

test("an owner leaving is told the cove survives without them", () => {
  renderSpace();
  expect(screen.getByText(/Ownership passes to another member/)).toBeDefined();
  expect(screen.getByText(/2 other people/)).toBeDefined();
});

test("a member leaving is told it stays for everyone else", () => {
  members = roster(3, false);
  renderSpace();
  expect(screen.getByText(/It stays for everyone else/)).toBeDefined();
});

test("leaving asks once, then posts", async () => {
  renderSpace();
  fireEvent.click(screen.getByText("Leave trip…"));
  expect(sent).toEqual([]);
  fireEvent.click(screen.getByText("Confirm — leave trip"));
  await waitFor(() => expect(navigated).toEqual(["/"]));
  expect(sent).toEqual([
    { method: "POST", path: "/api/spaces/trip/leave", body: undefined },
  ]);
});

test("alone in a cove, the exit is deletion and says what goes with it", () => {
  members = roster(1);
  renderSpace();
  expect(screen.getByText("Delete this cove")).toBeDefined();
  expect(screen.getByText(/pages, files, and history go with it, permanently/))
    .toBeDefined();
  expect(screen.queryByText("Leave trip…")).toBeNull();
});

test("deleting needs the cove's own name typed", () => {
  members = roster(1);
  renderSpace();
  fireEvent.click(screen.getByText("Delete trip…"));
  const confirm = screen.getByText("Permanently delete trip") as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText(/Type/), { target: { value: "trp" } });
  expect(confirm.disabled).toBe(true);
});

test("the typed name unlocks deletion, and it sends the confirmation", async () => {
  members = roster(1);
  renderSpace();
  fireEvent.click(screen.getByText("Delete trip…"));
  fireEvent.change(screen.getByLabelText(/Type/), { target: { value: "trip" } });
  fireEvent.click(screen.getByText("Permanently delete trip"));
  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toMatchObject({
    method: "DELETE",
    path: "/api/spaces/trip",
    body: { confirmation: "trip" },
  });
});

test("a refused exit is reported and the reader stays put", async () => {
  response = () => {
    throw new FakeApiError(403, "not_allowed");
  };
  renderSpace();
  fireEvent.click(screen.getByText("Leave trip…"));
  fireEvent.click(screen.getByText("Confirm — leave trip"));
  await waitFor(() => expect(screen.getByText("not_allowed")).toBeDefined());
  expect(navigated).toEqual([]);
});

test("the personal cove offers no way out at all", () => {
  // There is nothing to leave and nothing to hand over.
  renderSpace("personal");
  expect(screen.queryByText(/Leave this cove/)).toBeNull();
  expect(screen.queryByText(/Delete this cove/)).toBeNull();
});
