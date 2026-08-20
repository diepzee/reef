/**
 * The shell every signed-in screen renders inside.
 *
 * It owns the two things views cannot fetch for themselves — the signed-in
 * person, and the viewer's cove appearances — and both have to fail softly.
 * A cove always has a derived look, and the account row can sit blank, so
 * neither failure is worth an error surface; what would be wrong is letting
 * either take the whole app down with it.
 */

import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

class FakeApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Responses keyed by path, so one test can fail just one of the fetches. */
let responses: Record<string, () => unknown> = {};

/** Every `apiGet` call's path, in order — so a test can count refetches. */
let gets: string[] = [];
/** Every `apiSend` call, in order — so a test can assert what was posted. */
let sends: Array<{ method: string; path: string }> = [];

// Every mock of `../api` must present its whole surface — see the note in
// src/views/NewPage.test.tsx.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiGet: (path: string) => {
    gets.push(path);
    const handler = responses[path];
    if (!handler) return Promise.resolve({});
    try {
      return Promise.resolve(handler());
    } catch (error) {
      return Promise.reject(error);
    }
  },
  apiSend: (method: string, path: string) => {
    sends.push({ method, path });
    return Promise.resolve({});
  },
  apiDownload: () => Promise.resolve(),
}));

mock.module("../IndexProvider", () => ({
  useIndex: () => ({
    index: { coves: [] },
    error: null,
    refresh: () => Promise.resolve(),
  }),
}));
mock.module("../useMembers", () => ({
  useMembers: () => ({ members: null, error: null }),
}));
mock.module("../useMediaQuery", () => ({ useMediaQuery: () => true }));

const { AppShell } = await import("./AppShell");

function renderShell() {
  render(
    <MemoryRouter>
      <AppShell>
        <p>the view</p>
      </AppShell>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  responses = {
    "/api/me": () => ({
      person_id: "p1",
      email: "reader@example.com",
      display_name: "Wouter",
      avatar: null,
    }),
    "/api/appearance": () => ({ coves: {} }),
    "/api/release-notes": () => ({ entries: [], unread: false }),
  };
  gets = [];
  sends = [];
});

afterEach(cleanup);

test("it renders the view it is given", async () => {
  renderShell();
  expect(screen.getByText("the view")).toBeDefined();
  // Let the shell's three mount-time fetches settle before the test ends.
  // Unlike every other test here, this one asserts nothing that depends on
  // them and so never otherwise awaits — left alone, their resolutions land
  // after the test function returns but before `cleanup()` unmounts, which
  // is outside any `act()` scope and warns for all three.
  await waitFor(() => expect(screen.getAllByText("Wouter").length).toBeGreaterThan(0));
});

test("the signed-in person reaches the account row", async () => {
  renderShell();
  await waitFor(() => expect(screen.getAllByText("Wouter").length).toBeGreaterThan(0));
});

test("a failed appearance fetch costs only the overrides", async () => {
  // Every cove has a derived look, so this must not be an error surface --
  // and must not stop /api/me either.
  responses["/api/appearance"] = () => {
    throw new Error("offline");
  };
  renderShell();
  await waitFor(() => expect(screen.getAllByText("Wouter").length).toBeGreaterThan(0));
  expect(screen.getByText("the view")).toBeDefined();
});

test("a 401 leaves the shell standing rather than throwing", async () => {
  // apiGet already redirects to the login route for a 401; the shell must
  // not also blow up while that happens.
  responses["/api/me"] = () => {
    throw new FakeApiError(401, "unauthenticated");
  };
  renderShell();
  await waitFor(() => expect(screen.getByText("the view")).toBeDefined());
});

test("a person who never loads leaves the shell usable", async () => {
  responses["/api/me"] = () => {
    throw new FakeApiError(500, "server_error");
  };
  renderShell();
  await waitFor(() => expect(screen.getByText("the view")).toBeDefined());
});

test("opening the what's new panel marks it read once, without a second fetch", async () => {
  // Opening is reading: the dot clears from the POST's success alone, not
  // from a refetch of the feed — see the comment at AppShell.tsx's
  // `openReleaseNotes`. A second GET here would mean that guarantee broke.
  responses["/api/release-notes"] = () => ({ entries: [], unread: true });
  renderShell();
  await waitFor(() => expect(screen.getAllByText("Wouter").length).toBeGreaterThan(0));
  await waitFor(() => expect(screen.getByLabelText("unread")).toBeDefined());

  fireEvent.click(screen.getByRole("button", { name: /Wouter/ }));
  fireEvent.click(screen.getByRole("menuitem", { name: /what's new/i }));

  await waitFor(() =>
    expect(
      sends.some(
        (s) => s.method === "POST" && s.path === "/api/release-notes/seen",
      ),
    ).toBe(true),
  );
  await waitFor(() => expect(screen.queryByLabelText("unread")).toBeNull());
  expect(gets.filter((path) => path === "/api/release-notes").length).toBe(1);
});
