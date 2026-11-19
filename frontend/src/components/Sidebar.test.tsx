/**
 * The sidebar: the space list, and the actions that are not spaces.
 *
 * A dot is the mark of a space in this pane, so a link wearing one that
 * isn't a space reads as one — "New space" and "Invite someone" both used to
 * sit in the list and did exactly that. `spaceFolds.test.ts` owns the
 * fold-state arithmetic; what is only visible here is which rows are spaces,
 * and that the active one is marked as such.
 */

import { createContext } from "react";
import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AppearanceContext } from "../useAppearance";
import type { Me } from "../types";
import { ReleaseNotesContext } from "../useReleaseNotes";

let index: unknown = null;

mock.module("../IndexProvider", () => ({
  useIndex: () => ({ index, error: null, refresh: () => Promise.resolve() }),
}));
mock.module("../useMembers", () => ({
  useMembers: () => ({ members: null, error: null }),
}));
// Both exports: AppShell imports the context to provide it, and a mock
// missing it breaks that file at import time (see NewEngram.test.tsx).
mock.module("../useMembersSheet", () => ({
  MembersSheetContext: createContext<unknown>(null),
  useMembersSheet: () => ({ openMembers: () => {} }),
}));

const { Sidebar } = await import("./Sidebar");

const ME: Me = {
  person_id: "p1",
  email: "reader@example.com",
  display_name: "Wouter",
  avatar: null,
};

/** Render the sidebar as seen from `path`. */
function renderSidebar(path = "/") {
  render(
    <AppearanceContext.Provider
      value={{ appearance: {} as never, setAppearance: () => {} }}
    >
      {/* Sidebar renders AccountMenu, which calls useReleaseNotes(). Not
          mocked (unlike useMembersSheet above): a mocked module here raced
          against AccountMenu.test.tsx's own dynamic import of the real one
          when the whole suite ran, so a real provider is used instead. */}
      <ReleaseNotesContext.Provider
        value={{ unread: false, openReleaseNotes: () => {} }}
      >
        <MemoryRouter initialEntries={[path]}>
          <Sidebar me={ME} />
        </MemoryRouter>
      </ReleaseNotesContext.Provider>
    </AppearanceContext.Provider>,
  );
}

function space(alias: string) {
  return { alias, version: 1, engrams: [], attachments: [] };
}

beforeEach(() => {
  window.localStorage.clear();
  index = { spaces: [space("trip"), space("home")] };
});

afterEach(cleanup);

test("every space is linked", () => {
  renderSidebar();
  expect(screen.getByText("trip").closest("a")!.getAttribute("href")).toBe(
    "/s/trip",
  );
});

test("the index, new space, and invite are all reachable", () => {
  renderSidebar();
  expect(screen.getByText("Index").closest("a")!.getAttribute("href")).toBe(
    "/index",
  );
  expect(screen.getByText(/New space/).getAttribute("href")).toBe("/spaces/new");
  expect(screen.getByText(/Invite someone/).getAttribute("href")).toBe(
    "/invite",
  );
});

test("actions that are not spaces do not wear a space's dot", () => {
  // The dot means "this is a space". Lending it to an action makes the pane
  // lie about what it contains.
  renderSidebar();
  const newSpace = screen.getByText(/New space/);
  expect(newSpace.querySelector(".side-dot")).toBeNull();
  const invite = screen.getByText(/Invite someone/);
  expect(invite.querySelector(".side-dot")).toBeNull();
});

test("the index row is marked active when you are on it", () => {
  renderSidebar("/index");
  expect(screen.getByText("Index").closest("a")!.className).toContain("active");
});

test("the account menu sits in the sidebar", () => {
  renderSidebar();
  expect(screen.getByRole("button", { name: /Wouter/ })).toBeDefined();
});

test("a reef with no spaces yet still renders its actions", () => {
  // A brand-new account lands here before it has made anything.
  index = { spaces: [] };
  renderSidebar();
  expect(screen.getByText(/New space/)).toBeDefined();
});
