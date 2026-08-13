/**
 * The post-logout landing page, and the one link on it that must not
 * point at the login route.
 *
 * This page exists outside AppShell precisely so it never calls
 * `/api/me` — a 401 there redirects into `/api/auth/login` and undoes the
 * sign-out. The same trap applies to its link: after a deletion it has to
 * lead home, because sending someone to sign in again immediately after
 * erasing their account is both alarming and pointless.
 */

import { afterEach, expect, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import SignedOut from "./SignedOut";

/** Render at `search`, e.g. "?deleted=1". */
function renderAt(search: string) {
  render(
    <MemoryRouter initialEntries={[`/signed-out${search}`]}>
      <SignedOut />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

test("an ordinary sign-out offers the way back in", () => {
  renderAt("");
  expect(screen.getByText("Signed out")).toBeDefined();
  expect(screen.getByText("Sign in").getAttribute("href")).toBe(
    "/api/auth/login",
  );
});

test("after a deletion it leads home, not back to sign-in", () => {
  renderAt("?deleted=1");
  expect(screen.getByText("Your data was deleted")).toBeDefined();
  const link = screen.getByText("Return home");
  expect(link.getAttribute("href")).toBe("/");
});

test("a deletion says plainly what survives it", () => {
  // Shared coves outliving the account is the surprising part, so it is
  // stated rather than left to be discovered.
  renderAt("?deleted=1");
  expect(screen.getByText(/Shared coves remain with their other members/))
    .toBeDefined();
});

test("only deleted=1 counts as a deletion", () => {
  renderAt("?deleted=0");
  expect(screen.getByText("Signed out")).toBeDefined();
});
