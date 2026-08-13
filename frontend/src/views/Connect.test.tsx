/**
 * The connector setup page: the endpoint it hands out, and its copy button.
 *
 * The endpoint is derived from the current origin rather than written down,
 * because it has drifted once already — from the Railway hostname to
 * reefwith.me — silently breaking every connector configured against the
 * old one. A hard-coded address here would reintroduce exactly that, and
 * would be invisible in review, so it is pinned.
 */

import { afterEach, beforeEach, expect, test } from "bun:test";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import Connect from "./Connect";

/** What the fake clipboard was last given. */
let written: string[] = [];
/** Whether the fake clipboard refuses, as an insecure origin does. */
let refuse = false;

beforeEach(() => {
  written = [];
  refuse = false;
  Object.defineProperty(globalThis.navigator, "clipboard", {
    value: {
      writeText: (text: string) => {
        if (refuse) return Promise.reject(new Error("denied"));
        written.push(text);
        return Promise.resolve();
      },
    },
    configurable: true,
  });
});

afterEach(cleanup);

test("the endpoint comes from the current origin, never a written-down host", () => {
  render(<Connect />);
  expect(screen.getByText(`${window.location.origin}/mcp`)).toBeDefined();
});

test("copying puts the endpoint on the clipboard and says so", async () => {
  render(<Connect />);
  fireEvent.click(screen.getByText("Copy"));
  await waitFor(() => expect(screen.getByText("Copied")).toBeDefined());
  expect(written).toEqual([`${window.location.origin}/mcp`]);
});

test("a refused clipboard leaves the page usable, not stuck", async () => {
  // Browsers refuse this on insecure origins. The URL is on screen and
  // selectable regardless, so the only wrong outcome is claiming success.
  refuse = true;
  render(<Connect />);
  fireEvent.click(screen.getByText("Copy"));
  await waitFor(() => expect(screen.getByText("Copy")).toBeDefined());
  expect(screen.queryByText("Copied")).toBeNull();
});

test("it says what an assistant can reach, and links the privacy page", () => {
  // This page is where a member learns their pages leave the system, so
  // the disclosure and its link are part of the contract, not decoration.
  render(<Connect />);
  expect(screen.getByText(/sent to whoever runs it/)).toBeDefined();
  expect(screen.getByText("the privacy page").getAttribute("href")).toBe(
    "/privacy",
  );
});
