/**
 * Choosing a new page's path: the part of the form that answers while you
 * type, and the handover to the editor once the path is settled.
 *
 * `pagePath.test.ts` covers normalizing and judging a path as pure
 * functions. What is only visible by rendering is whether the answer
 * reaches the screen, whether the button is disabled when it should be,
 * and — the one that matters — that the path the editor goes on to save
 * under is the normalized one. The editor cannot rename a page, so a path
 * that is wrong here is wrong forever.
 *
 * The real editor is rendered rather than a stand-in. That is partly a
 * better test (it follows the path all the way to the request) and partly
 * a constraint: `mock.module` replaces a module for the whole `bun test`
 * run, so stubbing `./Editor` here would hand the stub to `Editor.test.tsx`
 * as well. Each component is imported by exactly one test file.
 */

import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

let sent: Array<{ method: string; path: string; body?: unknown }> = [];

// Every mock of `../api` must present its whole surface. `mock.module`
// replaces the module for the entire `bun test` run, so a partial mock here
// becomes the module some *other* view imports, and it fails at import time
// with "Export named 'apiDownload' not found" — a long way from the file
// that caused it. Unused entries below are deliberate.
mock.module("../api", () => ({
  ApiError: class extends Error {},
  apiGet: () => Promise.resolve({}),
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve({});
  },
  apiDownload: () => Promise.resolve(),
}));

mock.module("../IndexProvider", () => ({
  useIndex: () => ({ refresh: () => Promise.resolve() }),
}));

const { default: NewPage } = await import("./NewPage");

function renderNewPage() {
  render(
    <MemoryRouter initialEntries={["/s/trip/new"]}>
      <Routes>
        <Route path="/s/:space/new" element={<NewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Type `value` into the path box. */
function type(value: string) {
  fireEvent.change(screen.getByLabelText("Path"), { target: { value } });
}

/** The Continue button. */
function submit(): HTMLButtonElement {
  return screen.getByText("Continue") as HTMLButtonElement;
}

beforeEach(() => {
  sent = [];
});

afterEach(cleanup);

test("an empty path cannot be submitted", () => {
  renderNewPage();
  expect(submit().disabled).toBe(true);
});

test("a reserved path is refused with its reason, not silently", () => {
  renderNewPage();
  type("meta/protocol");
  expect(submit().disabled).toBe(true);
  expect(screen.getByLabelText("Path").getAttribute("aria-invalid")).toBe(
    "true",
  );
  // The hint is aria-live, so the reason is announced rather than only seen.
  expect(document.querySelector(".ed-problem")).not.toBeNull();
});

test("a path that will be tidied says so before it is accepted", () => {
  renderNewPage();
  type("packing list");
  expect(screen.getByText(/Will be created as/)).toBeDefined();
  expect(submit().disabled).toBe(false);
});

test("a bad path never reaches the editor", () => {
  renderNewPage();
  type("meta/protocol");
  fireEvent.click(submit());
  expect(screen.getByText("New page")).toBeDefined();
  // Still the path form, not the editor's body field.
  expect(screen.queryByLabelText("Body")).toBeNull();
});

test("continuing opens the editor for the chosen path", () => {
  renderNewPage();
  type("packing list");
  fireEvent.click(submit());
  expect(screen.getByLabelText("Body")).toBeDefined();
});

test("the page is saved under the normalized path, not what was typed", async () => {
  // The whole point of confirming a path first: the editor has no way to
  // rename, so this URL is the page's identity from here on.
  renderNewPage();
  type("packing list");
  fireEvent.click(submit());
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "hi" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(sent.length).toBeGreaterThan(0));
  expect(sent[0]!.path).toBe("/api/pages/trip/packing-list.md");
  // A brand-new page has no version to expect.
  expect(sent[0]!.body).toMatchObject({ expected_version: null });
});
