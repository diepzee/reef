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
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

// Every mock of `../api` must present its whole surface — see the note in
// src/views/NewPage.test.tsx.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiGet: (path: string) => {
    const handler = responses[path];
    if (!handler) return Promise.resolve({});
    try {
      return Promise.resolve(handler());
    } catch (error) {
      return Promise.reject(error);
    }
  },
  apiSend: () => Promise.resolve({}),
  apiDownload: () => Promise.resolve(),
}));

mock.module("../IndexProvider", () => ({
  useIndex: () => ({
    index: { spaces: [] },
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
  };
});

afterEach(cleanup);

test("it renders the view it is given", () => {
  renderShell();
  expect(screen.getByText("the view")).toBeDefined();
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
