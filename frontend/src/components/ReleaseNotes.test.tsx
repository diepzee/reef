/**
 * The what's-new panel: what it shows, and how it closes.
 *
 * It is a dialog, so it has to say so — a reader on a screen reader gets no
 * signal otherwise — and Escape has to close it, because every other
 * dismissible surface in the app closes that way.
 */

import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { ReleaseEntry } from "../types";
import { ReleaseNotes } from "./ReleaseNotes";

afterEach(cleanup);

const ENTRIES: ReleaseEntry[] = [
  {
    version: "0.5.0",
    date: "2026-08-18",
    changes: [{ kind: "added", text: "Search your pages from your assistant." }],
  },
  {
    version: "0.4.0",
    date: "2026-08-17",
    changes: [{ kind: "fixed", text: "Pictures upload again." }],
  },
];

test("it lists every release, newest first", () => {
  render(<ReleaseNotes entries={ENTRIES} onClose={() => {}} />);
  const versions = screen.getAllByRole("heading", { level: 3 });
  expect(versions.map((h) => h.textContent)).toEqual([
    expect.stringContaining("0.5.0"),
    expect.stringContaining("0.4.0"),
  ]);
  expect(screen.getByText("Search your pages from your assistant.")).toBeDefined();
});

test("it announces itself as a dialog", () => {
  render(<ReleaseNotes entries={ENTRIES} onClose={() => {}} />);
  expect(screen.getByRole("dialog")).toBeDefined();
});

test("Escape closes it", () => {
  let closed = false;
  render(<ReleaseNotes entries={ENTRIES} onClose={() => (closed = true)} />);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(closed).toBe(true);
});

test("an empty feed reads as a sentence, not as a blank panel", () => {
  render(<ReleaseNotes entries={[]} onClose={() => {}} />);
  expect(screen.getByText(/nothing to report yet/i)).toBeDefined();
});
