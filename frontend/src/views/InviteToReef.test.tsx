/**
 * Inviting someone to reef itself, and the shared budget in front of it.
 *
 * The budget is fetched up front so the ceiling is visible before someone
 * runs into it, and a 429 is a routine outcome rather than a failure — its
 * message names the date the budget unlocks, so replacing it with a generic
 * "could not invite" would throw away the only useful part and read as a
 * bug in the app.
 */

import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

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
let budget: unknown = { invites_left: 3 };
let respond: () => unknown = () => ({
  email: "new@example.com",
  already_known: false,
  next_step: "Tell them to check their email.",
  invites_left: 2,
});

// Every mock of `../api` must present its whole surface — see the note in
// src/views/NewPage.test.tsx.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiGet: () => Promise.resolve(budget),
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve(respond());
  },
  apiDownload: () => Promise.resolve(),
}));

const { default: InviteToReef } = await import("./InviteToReef");

/** Fill the form in and submit it. */
function invite(email = "new@example.com") {
  fireEvent.change(screen.getByLabelText(/Email/i), { target: { value: email } });
  fireEvent.click(screen.getByRole("button", { name: /Invite/ }));
}

beforeEach(() => {
  sent = [];
  budget = { invites_left: 3 };
  respond = () => ({
    email: "new@example.com",
    already_known: false,
    next_step: "Tell them to check their email.",
    invites_left: 2,
  });
});

afterEach(cleanup);

test("an empty address cannot be submitted", () => {
  render(<InviteToReef />);
  expect(
    (screen.getByRole("button", { name: /Invite/ }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

test("a successful invite reports who, and what happens next", async () => {
  render(<InviteToReef />);
  invite();
  await waitFor(() => expect(screen.getByText("new@example.com")).toBeDefined());
  expect(screen.getByText(/is now invited/)).toBeDefined();
  expect(screen.getByText(/check their email/)).toBeDefined();
  expect(sent[0]).toMatchObject({
    method: "POST",
    path: "/api/invites",
    body: { email: "new@example.com", display_name: null },
  });
});

test("someone already on reef is described as such, not as newly invited", async () => {
  respond = () => ({
    email: "old@example.com",
    already_known: true,
    next_step: "They can already sign in.",
    invites_left: 2,
  });
  render(<InviteToReef />);
  invite("old@example.com");
  await waitFor(() =>
    expect(screen.getByText(/was already on/)).toBeDefined(),
  );
  expect(screen.queryByText(/is now invited/)).toBeNull();
});

test("a spent budget shows the reason it gives, including the unlock date", async () => {
  respond = () => {
    throw new FakeApiError(
      429,
      "invite_budget",
      "no invites left until 1 September 2026",
    );
  };
  render(<InviteToReef />);
  invite();
  await waitFor(() =>
    expect(
      screen.getByText(/no invites left until 1 September 2026/),
    ).toBeDefined(),
  );
});

test("the form clears after a success so the next invite starts fresh", async () => {
  render(<InviteToReef />);
  invite();
  await waitFor(() => expect(screen.getByText(/is now invited/)).toBeDefined());
  expect((screen.getByLabelText(/Email/i) as HTMLInputElement).value).toBe("");
});

test("a budget that cannot be fetched does not break the form", async () => {
  // The count is a courtesy; losing it must not cost the ability to invite.
  budget = null;
  render(<InviteToReef />);
  invite();
  await waitFor(() => expect(sent.length).toBe(1));
});
