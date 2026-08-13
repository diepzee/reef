/**
 * The account menu: what it opens, and that it announces itself properly.
 *
 * The menu is a popup, so the trigger has to report whether it is open —
 * without `aria-expanded` a screen-reader user gets no signal that anything
 * happened. Sign-out is the one irreversible-ish item and lives behind a
 * separator from the navigation items.
 */

import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { Me } from "../types";

let sent: Array<{ method: string; path: string }> = [];

// Every mock of `../api` must present its whole surface — see the note in
// src/views/NewPage.test.tsx.
mock.module("../api", () => ({
  ApiError: class extends Error {},
  apiGet: () => Promise.resolve({}),
  apiSend: (method: string, path: string) => {
    sent.push({ method, path });
    return Promise.resolve({});
  },
  apiDownload: () => Promise.resolve(),
}));

const { AccountMenu } = await import("./AccountMenu");

const ME: Me = {
  person_id: "p1",
  email: "reader@example.com",
  display_name: "Wouter",
  avatar: null,
};

function renderMenu(me: Me | null = ME) {
  render(
    <MemoryRouter>
      <AccountMenu me={me} />
    </MemoryRouter>,
  );
}

/** The trigger button. */
function trigger(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Wouter/ }) as HTMLButtonElement;
}

beforeEach(() => {
  sent = [];
});

afterEach(cleanup);

test("it starts closed, and says so", () => {
  renderMenu();
  expect(trigger().getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("menu")).toBeNull();
});

test("opening reveals the menu and updates the announcement", () => {
  renderMenu();
  fireEvent.click(trigger());
  expect(trigger().getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByRole("menu")).toBeDefined();
});

test("the menu holds profile, export, and sign-out", () => {
  renderMenu();
  fireEvent.click(trigger());
  expect(screen.getByText("Profile").getAttribute("href")).toBe("/profile");
  expect(screen.getByText("Export").getAttribute("href")).toBe("/export");
  expect(screen.getByText("Sign out")).toBeDefined();
  // Sign out is separated from the navigation items rather than sitting
  // among them, where it would be easy to hit by accident.
  expect(document.querySelector('[role="separator"]')).not.toBeNull();
});

test("clicking the trigger again closes it", () => {
  renderMenu();
  fireEvent.click(trigger());
  fireEvent.click(trigger());
  expect(screen.queryByRole("menu")).toBeNull();
});

test("signing out posts to the sign-out route", () => {
  renderMenu();
  fireEvent.click(trigger());
  fireEvent.click(screen.getByText("Sign out"));
  expect(sent.length).toBe(1);
  expect(sent[0]!.path).toContain("logout");
});

test("a person who has not loaded yet does not break the menu", () => {
  // AppShell renders this before /api/me resolves.
  renderMenu(null);
  expect(screen.getByRole("button")).toBeDefined();
});
