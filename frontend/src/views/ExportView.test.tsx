/**
 * Exporting your data, and the guards in front of destroying it.
 *
 * Account deletion is the most irreversible thing this app can do, and it
 * is guarded by three independent conditions: the zone has to be revealed,
 * a checkbox ticked, and the word DELETE typed exactly. Each one is a
 * single boolean away from being useless, and none of them is visible in a
 * diff of the request — so every one is pinned here, including the ways
 * they must *not* pass.
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
let downloads: Array<{ path: string; body: unknown; name: string }> = [];
let deleteResponse: () => unknown = () => ({ deleted: true });
let downloadFails: Error | null = null;

// Every mock of `../api` must present its whole surface. `mock.module`
// replaces the module for the entire `bun test` run, so a partial mock here
// becomes the module some *other* view imports, and it fails at import time
// with "Export named 'apiDownload' not found" — a long way from the file
// that caused it. Unused entries below are deliberate.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve(deleteResponse());
  },
  apiDownload: (path: string, body: unknown, name: string) => {
    downloads.push({ path, body, name });
    return downloadFails ? Promise.reject(downloadFails) : Promise.resolve();
  },
  apiGet: () => Promise.resolve({}),
}));

mock.module("../IndexProvider", () => ({
  useIndex: () => ({
    rawIndex: { spaces: [{ alias: "trip" }, { alias: "home" }] },
    error: null,
    refresh: () => Promise.resolve(),
  }),
}));

const { default: ExportView } = await import("./ExportView");

/** Reveal the danger zone and satisfy `n` of the two guards. */
function arm({ tick = false, phrase = "" } = {}) {
  fireEvent.click(screen.getByText("Delete my data…"));
  if (tick) fireEvent.click(screen.getByRole("checkbox"));
  if (phrase)
    fireEvent.change(screen.getByLabelText(/Type/), {
      target: { value: phrase },
    });
}

/** The final, irreversible button. */
function finalButton(): HTMLButtonElement {
  return screen.getByText("Permanently delete my data") as HTMLButtonElement;
}

beforeEach(() => {
  sent = [];
  downloads = [];
  downloadFails = null;
  deleteResponse = () => ({ deleted: true });
  render(<ExportView />);
});

afterEach(cleanup);

test("an export can be scoped to one cove", async () => {
  fireEvent.change(screen.getByLabelText("Coves"), { target: { value: "trip" } });
  fireEvent.click(screen.getByText("Download export"));
  await waitFor(() => expect(downloads.length).toBe(1));
  expect(downloads[0]!.body).toMatchObject({ scope: "trip", format: "markdown" });
});

test("a failed export says so rather than appearing to have worked", async () => {
  downloadFails = new FakeApiError(500, "server_error");
  fireEvent.click(screen.getByText("Download export"));
  await waitFor(() =>
    expect(screen.getByText("Download export")).toBeDefined(),
  );
  expect(document.querySelector(".notice")).not.toBeNull();
});

test("deletion is not even offered until the zone is opened", () => {
  expect(screen.queryByText("Permanently delete my data")).toBeNull();
});

test("opening the zone says what survives the deletion", () => {
  // Shared coves outliving the account, and ownership moving to someone
  // else, are the parts a person cannot guess.
  arm();
  expect(screen.getByText(/Shared coves with other members will remain/))
    .toBeDefined();
});

test("neither guard alone unlocks it", () => {
  arm({ tick: true });
  expect(finalButton().disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox")); // untick
  fireEvent.change(screen.getByLabelText(/Type/), {
    target: { value: "DELETE" },
  });
  expect(finalButton().disabled).toBe(true);
});

test("a near-miss phrase does not count", () => {
  // Case and whitespace are not forgiven -- this is the last thing standing
  // between a person and permanent loss.
  for (const phrase of ["delete", "Delete", " DELETE", "DELETE "]) {
    arm({ tick: true, phrase });
    expect(finalButton().disabled).toBe(true);
    fireEvent.click(screen.getByText("Cancel"));
  }
});

test("both guards together unlock it, and it deletes", async () => {
  arm({ tick: true, phrase: "DELETE" });
  expect(finalButton().disabled).toBe(false);
  fireEvent.click(finalButton());
  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toMatchObject({
    method: "POST",
    path: "/api/account/delete",
    body: { confirmation: "DELETE" },
  });
});

test("cancelling clears the guards rather than leaving them armed", () => {
  arm({ tick: true, phrase: "DELETE" });
  fireEvent.click(screen.getByText("Cancel"));
  arm();
  // Reopened from scratch: nothing carried over from the previous attempt.
  expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
  expect(finalButton().disabled).toBe(true);
});

test("a refused deletion is reported and nothing is assumed gone", async () => {
  deleteResponse = () => {
    throw new FakeApiError(409, "cannot_delete");
  };
  arm({ tick: true, phrase: "DELETE" });
  fireEvent.click(finalButton());
  await waitFor(() => expect(screen.getByText("cannot_delete")).toBeDefined());
});
