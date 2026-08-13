/**
 * Creating a cove: what happens on success, and what a refusal looks like.
 *
 * A 400 here is routine rather than exceptional — a slug can be malformed
 * or already taken — so the reason has to reach the form. The detail is the
 * only actionable part of that answer, which is the same lesson the avatar
 * endpoint taught: see `BadRequest` in `src/rif/web/routes_api.py`.
 */

import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

let sent: Array<{ method: string; path: string; body?: unknown }> = [];
let respond: () => unknown = () => ({ alias: "trip", slug: "trip" });
let navigated: string[] = [];
let refreshed = 0;

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

// Every mock of `../api` must present its whole surface. `mock.module`
// replaces the module for the entire `bun test` run, so a partial mock here
// becomes the module some *other* view imports, and it fails at import time
// with "Export named 'apiDownload' not found" — a long way from the file
// that caused it. Unused entries below are deliberate.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve(respond());
  },
  apiGet: () => Promise.resolve({}),
  apiDownload: () => Promise.resolve(),
}));

mock.module("../IndexProvider", () => ({
  useIndex: () => ({
    refresh: () => {
      refreshed += 1;
      return Promise.resolve();
    },
  }),
}));

mock.module("react-router-dom", () => ({
  ...require("react-router-dom"),
  useNavigate: () => (to: string) => navigated.push(to),
}));

const { default: NewSpace } = await import("./NewSpace");

function renderNewSpace() {
  render(
    <MemoryRouter>
      <NewSpace />
    </MemoryRouter>,
  );
}

/** Type `value` into the name box. */
function type(value: string) {
  fireEvent.change(screen.getByLabelText("Name"), { target: { value } });
}

beforeEach(() => {
  sent = [];
  navigated = [];
  refreshed = 0;
  respond = () => ({ alias: "trip", slug: "trip" });
});

afterEach(cleanup);

test("cannot submit an empty name", () => {
  renderNewSpace();
  expect(
    (screen.getByText("Create cove") as HTMLButtonElement).disabled,
  ).toBe(true);
});

test("creating navigates to the new cove and refreshes the index", async () => {
  // The index has to be refetched before navigating, or the cove the person
  // was just sent to is missing from the sidebar they land next to.
  renderNewSpace();
  type("trip");
  fireEvent.click(screen.getByText("Create cove"));
  await waitFor(() => expect(navigated).toEqual(["/s/trip"]));
  expect(sent).toEqual([
    { method: "POST", path: "/api/spaces", body: { slug: "trip" } },
  ]);
  expect(refreshed).toBe(1);
});

test("a refusal is shown with its reason and the form stays usable", async () => {
  respond = () => {
    throw new FakeApiError(400, "space_error", "that name is already taken");
  };
  renderNewSpace();
  type("trip");
  fireEvent.click(screen.getByText("Create cove"));
  await waitFor(() =>
    expect(screen.getByText("that name is already taken")).toBeDefined(),
  );
  // Still on the form, and able to try again rather than stuck on "Creating…".
  expect(navigated).toEqual([]);
  expect(
    (screen.getByText("Create cove") as HTMLButtonElement).disabled,
  ).toBe(false);
});

test("a failure that is not from the API still says something", async () => {
  respond = () => {
    throw new Error("network down");
  };
  renderNewSpace();
  type("trip");
  fireEvent.click(screen.getByText("Create cove"));
  await waitFor(() =>
    expect(screen.getByText("could not create the cove")).toBeDefined(),
  );
});
