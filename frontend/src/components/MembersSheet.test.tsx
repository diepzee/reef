/**
 * The roster sheet: who can see a cove, and who may change that.
 *
 * Two things here are privacy boundaries rather than styling. Only an owner
 * may invite or remove, so a non-owner must not be shown those controls at
 * all. And an invite returns a disclosure — what the invited person will be
 * able to read — which has to reach the screen, because it is the moment
 * the inviter learns what they are handing over.
 *
 * The sheet also stays mounted across a close, so its transient state has
 * to be cleared by hand; a stale disclosure reappearing later would report
 * an invite that did not just happen.
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
let respond: () => unknown = () => ({ disclosure: "" });
let members: unknown = null;

// Every mock of `../api` must present its whole surface — see the note in
// src/views/NewPage.test.tsx.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiGet: () => Promise.resolve({}),
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve(respond());
  },
  apiDownload: () => Promise.resolve(),
}));

mock.module("../useMembers", () => ({
  useMembers: () => ({
    members,
    error: null,
    refresh: () => Promise.resolve(),
  }),
}));
mock.module("../IndexProvider", () => ({
  useIndex: () => ({ refresh: () => Promise.resolve() }),
}));
mock.module("../useMediaQuery", () => ({ useMediaQuery: () => true }));

const { MembersSheet } = await import("./MembersSheet");

/** Render the sheet, open unless told otherwise. */
function renderSheet(open = true, onClose = () => {}) {
  return render(
    <MemoryRouter>
      <MembersSheet space="trip" open={open} onClose={onClose} />
    </MemoryRouter>,
  );
}

/** A roster where the viewer is or is not the owner. */
function roster(is_owner: boolean) {
  return {
    is_owner,
    owner_email: is_owner ? "own@example.com" : "",
    members: [
      { display_name: "Ada", email: is_owner ? "own@example.com" : "" },
      { display_name: "Guest", email: is_owner ? "guest@example.com" : "" },
    ],
  };
}

beforeEach(() => {
  sent = [];
  respond = () => ({ disclosure: "" });
  members = roster(true);
});

afterEach(cleanup);

test("everyone in the cove is listed", () => {
  renderSheet();
  expect(screen.getByText("Ada")).toBeDefined();
  expect(screen.getByText("Guest")).toBeDefined();
});

test("an owner sees who owns it and can remove the others", () => {
  renderSheet();
  // The owner's own row is tagged, and carries no Remove: exactly one
  // Remove control exists, and it belongs to the other person.
  expect(document.querySelectorAll(".mbs-owner-tag").length).toBe(1);
  expect(screen.getAllByText("Remove…").length).toBe(1);
});

test("a non-owner is offered no way to invite or remove anyone", () => {
  // The API refuses these anyway; showing them would promise something the
  // person cannot do, and leak who is privileged.
  members = roster(false);
  renderSheet();
  expect(screen.queryByText("Remove…")).toBeNull();
  // And with every email blanked, nobody is wrongly tagged as the owner.
  expect(document.querySelectorAll(".mbs-owner-tag").length).toBe(0);
  expect(screen.queryByLabelText(/Email/i)).toBeNull();
});

test("removing asks before it acts", () => {
  renderSheet();
  fireEvent.click(screen.getByText("Remove…"));
  expect(sent).toEqual([]);
  expect(screen.getByText("Confirm remove")).toBeDefined();
});

test("confirming a removal sends it for that person only", async () => {
  renderSheet();
  fireEvent.click(screen.getByText("Remove…"));
  fireEvent.click(screen.getByText("Confirm remove"));
  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toMatchObject({
    method: "DELETE",
    path: "/api/spaces/trip/members/guest%40example.com",
  });
});

test("cancelling a removal sends nothing", () => {
  renderSheet();
  fireEvent.click(screen.getByText("Remove…"));
  fireEvent.click(screen.getByText("Cancel"));
  expect(sent).toEqual([]);
  expect(screen.getByText("Remove…")).toBeDefined();
});

test("a failed removal is reported rather than silently ignored", async () => {
  respond = () => {
    throw new FakeApiError(403, "not_allowed");
  };
  renderSheet();
  fireEvent.click(screen.getByText("Remove…"));
  fireEvent.click(screen.getByText("Confirm remove"));
  await waitFor(() => expect(screen.getByText("not_allowed")).toBeDefined());
});

test("Escape closes the sheet", () => {
  let closed = 0;
  renderSheet(true, () => (closed += 1));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(closed).toBe(1);
});

test("Escape does nothing while the sheet is shut", () => {
  // The listener is only wired while open, so it cannot shadow Escape
  // elsewhere in the app.
  let closed = 0;
  renderSheet(false, () => (closed += 1));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(closed).toBe(0);
});
