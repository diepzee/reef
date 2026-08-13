/**
 * Choosing how a cove looks to you alone.
 *
 * The choice is applied optimistically — the point is watching the sidebar
 * change — which means the only thing standing between a failed save and a
 * lie on screen is the rollback. If that stops working, the picker shows a
 * colour the server never stored and nothing says otherwise.
 */

import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { AppearanceContext } from "../useAppearance";

let sent: Array<{ method: string; path: string; body?: unknown }> = [];
let fails = false;

// Every mock of `../api` must present its whole surface — see the note in
// src/views/NewPage.test.tsx.
mock.module("../api", () => ({
  ApiError: class extends Error {},
  apiGet: () => Promise.resolve({}),
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return fails ? Promise.reject(new Error("no")) : Promise.resolve({});
  },
  apiDownload: () => Promise.resolve(),
}));

const { LookPicker } = await import("./LookPicker");

/** Every appearance the provider was asked to record, in order. */
let recorded: Array<{ alias: string; look: unknown }> = [];

function renderPicker(appearance: Record<string, unknown> = {}) {
  render(
    <AppearanceContext.Provider
      value={{
        appearance: appearance as never,
        setAppearance: (alias, look) => recorded.push({ alias, look }),
      }}
    >
      <LookPicker alias="trip" />
    </AppearanceContext.Provider>,
  );
}

beforeEach(() => {
  sent = [];
  recorded = [];
  fails = false;
});

afterEach(cleanup);

test("it renders the two rows and nothing around them", () => {
  // "Only you" is the caller's to say, once, over the whole section it puts
  // this in — the sheet's Appearance heading covers the name too. Said here
  // as well it appeared twice under one heading. MembersSheet.test.tsx
  // holds the assertion that it is still said.
  renderPicker();
  expect(screen.queryByText(/Only to you/)).toBeNull();
  expect(screen.getAllByRole("group").length).toBe(2);
});

test("choosing a colour records it and saves it", async () => {
  renderPicker();
  const swatches = screen.getAllByRole("group", { name: "Colour" });
  const buttons = swatches[0]!.querySelectorAll("button");
  fireEvent.click(buttons[1]!);
  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toMatchObject({
    method: "PUT",
    path: "/api/spaces/trip/appearance",
  });
  // Applied before the round trip, not after it.
  expect(recorded.length).toBeGreaterThan(0);
});

test("a failed save is rolled back and reported", async () => {
  // Two recordings: the optimistic one, then the restore.
  fails = true;
  renderPicker();
  const group = screen.getAllByRole("group", { name: "Colour" })[0]!;
  fireEvent.click(group.querySelectorAll("button")[1]!);
  await waitFor(() =>
    expect(screen.getByText("that could not be saved")).toBeDefined(),
  );
  expect(recorded.length).toBe(2);
  expect(recorded[1]!.look).toEqual({ color: null, glyph: null });
});

test("the derive-from-name options are offered for both colour and creature", () => {
  // Without these there is no way back to the default once something is
  // picked.
  renderPicker();
  expect(screen.getByLabelText("Colour from its name")).toBeDefined();
  expect(screen.getByLabelText("Icon from its name")).toBeDefined();
});

test("the chosen colour is marked by something other than colour", () => {
  // The tiles are colours, so a ring around one reads as another shade
  // rather than as a state; the check is what actually says "this one".
  renderPicker({ trip: { color: "amber", glyph: null } });
  const chosen = screen.getByLabelText("amber");
  expect(chosen.querySelector("svg.look-tick")).not.toBeNull();
  expect(screen.getByLabelText("indigo").querySelector("svg.look-tick")).toBeNull();
});
