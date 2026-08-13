/**
 * The picture-upload flow, from choosing a file to what the person is told.
 *
 * This view is why component tests exist here at all: an avatar upload
 * failed with a bare "bad request", and nothing in the suite could have
 * caught it — the encoding lived inside a component no test could render,
 * so it had to be extracted to `avatarEncode.ts` before it could be pinned
 * at all. What is tested here is the part that stayed behind: which message
 * reaches the screen, and what gets sent to the API.
 */

import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { MeContext } from "../useMe";
import type { Me } from "../types";

/** A person with no picture yet. */
const PERSON: Me = {
  person_id: "p1",
  email: "reader@example.com",
  display_name: "Wouter",
  avatar: null,
};

/** Calls the API client recorded during a test. */
let sent: Array<{ method: string; path: string; body?: unknown }> = [];
/** What the next apiSend call should do. */
let respond: () => unknown = () => ({ avatar: "/api/me/avatar?v=10" });

// Every mock of `../api` must present its whole surface. `mock.module`
// replaces the module for the entire `bun test` run, so a partial mock here
// becomes the module some *other* view imports, and it fails at import time
// with "Export named 'apiDownload' not found" — a long way from the file
// that caused it. Unused entries below are deliberate.
mock.module("../api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    code: string;
    detail?: string;
    constructor(status: number, code: string, detail?: string) {
      super(detail ? `${code}: ${detail}` : code);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
      this.detail = detail;
    }
  },
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve(respond());
  },
  apiGet: () => Promise.resolve({}),
  apiDownload: () => Promise.resolve(),
}));

const { ApiError } = await import("../api");
const { default: Profile } = await import("./Profile");

/**
 * Render Profile for `me`, returning the avatar values it pushes back.
 *
 * :param me: the person in context, or null for "still loading"
 */
function renderProfile(me: Me | null = PERSON) {
  const changes: Array<string | null> = [];
  render(
    <MeContext.Provider value={{ me, setAvatar: (a) => changes.push(a) }}>
      <Profile />
    </MeContext.Provider>,
  );
  return changes;
}

/** Drop a file onto the view's file input, as a picker would. */
function choose(file: File): void {
  const input = document.querySelector<HTMLInputElement>("input[type=file]")!;
  // fireEvent rather than a raw dispatch: it wraps the update in act(), so
  // React's own warning cannot mask a genuine problem in a later test.
  fireEvent.change(input, { target: { files: [file] } });
}

beforeEach(() => {
  sent = [];
  respond = () => ({ avatar: "/api/me/avatar?v=10" });
});

afterEach(cleanup);

test("shows the person's name and address", () => {
  renderProfile();
  expect(screen.getByText("Wouter")).toBeDefined();
  expect(screen.getByText("reader@example.com")).toBeDefined();
});

test("offers to choose a picture when there is none", () => {
  renderProfile();
  expect(screen.getByText("Choose a picture")).toBeDefined();
  // Nothing to remove yet, so no Remove button.
  expect(screen.queryByText("Remove")).toBeNull();
});

test("offers to change and remove once a picture is set", () => {
  renderProfile({ ...PERSON, avatar: "/api/me/avatar?v=10" });
  expect(screen.getByText("Change picture")).toBeDefined();
  expect(screen.getByText("Remove")).toBeDefined();
});

test("the picker only offers the types the endpoint stores", () => {
  renderProfile();
  const input = document.querySelector<HTMLInputElement>("input[type=file]")!;
  // No SVG: it is a script carrier and the endpoint serves bytes back to a
  // browser. See AVATAR_MIMES in src/rif/web/routes_api.py.
  expect(input.getAttribute("accept")).toBe(
    "image/png,image/jpeg,image/webp,image/gif",
  );
});

test("removing a picture clears it through the API", async () => {
  respond = () => ({ avatar: null });
  const changes = renderProfile({ ...PERSON, avatar: "/api/me/avatar?v=10" });
  fireEvent.click(screen.getByText("Remove"));
  await waitFor(() => expect(changes).toEqual([null]));
  expect(sent).toEqual([
    { method: "DELETE", path: "/api/me/avatar", body: undefined },
  ]);
});

test("a server refusal is shown verbatim, not swallowed", async () => {
  // The original bug's other half: the endpoint writes a reason worth
  // reading, and the view must not replace it with something vaguer.
  respond = () => {
    throw new ApiError(400, "bad_request", "a picture may be at most 512kB");
  };
  renderProfile({ ...PERSON, avatar: "/api/me/avatar?v=10" });
  fireEvent.click(screen.getByText("Remove"));
  await waitFor(() =>
    expect(screen.getByText("that picture could not be removed")).toBeDefined(),
  );
});

test("an unreadable file is reported without reaching the API", async () => {
  // createImageBitmap does not exist in this DOM, which is exactly what a
  // browser handed a file it cannot decode amounts to.
  renderProfile();
  choose(new File(["not an image"], "x.png", { type: "image/png" }));
  await waitFor(() =>
    expect(screen.getByText("that picture could not be read")).toBeDefined(),
  );
  expect(sent).toEqual([]);
});
