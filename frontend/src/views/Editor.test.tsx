/**
 * Saving a page, and what happens when someone else saved it first.
 *
 * The conflict path is the one worth pinning: a 409 must never cost the
 * author their typing. The contract is that the draft is left exactly as
 * written, the latest saved body appears alongside it for comparison, and
 * the expected version advances so the *next* save applies cleanly. Every
 * one of those is silent if it breaks — the save would simply start
 * discarding work — so none of it is checkable without rendering.
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
let saveResponse: () => unknown = () => ({});
let getResponse: () => unknown = () => ({
  title: "Packing",
  tags: [],
  body: "their text",
  version: 7,
});
let navigated: string[] = [];

// Every mock of `../api` must present its whole surface. `mock.module`
// replaces the module for the entire `bun test` run, so a partial mock here
// becomes the module some *other* view imports, and it fails at import time
// with "Export named 'apiDownload' not found" — a long way from the file
// that caused it. Unused entries below are deliberate.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiGet: (path: string) => {
    sent.push({ method: "GET", path });
    return Promise.resolve(getResponse());
  },
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve(saveResponse());
  },
  apiDownload: () => Promise.resolve(),
}));

mock.module("../IndexProvider", () => ({
  useIndex: () => ({ refresh: () => Promise.resolve() }),
}));

mock.module("react-router-dom", () => ({
  ...require("react-router-dom"),
  useNavigate: () => (to: string) => navigated.push(to),
}));

const { PageEditor } = await import("./Editor");

function renderEditor(version: number | null = 3) {
  render(
    <MemoryRouter>
      <PageEditor
        space="trip"
        path="packing.md"
        mode="edit"
        initialTitle="Packing"
        initialTags={["gear"]}
        initialBody="my text"
        initialVersion={version}
      />
    </MemoryRouter>,
  );
}

/** The body textarea. */
function bodyBox(): HTMLTextAreaElement {
  return screen.getByLabelText("Body") as HTMLTextAreaElement;
}

/** The most recent PUT the editor made. */
function lastSave() {
  return [...sent].reverse().find((call) => call.method === "PUT");
}

beforeEach(() => {
  sent = [];
  navigated = [];
  saveResponse = () => ({});
  getResponse = () => ({
    title: "Packing",
    tags: [],
    body: "their text",
    version: 7,
  });
});

afterEach(cleanup);

test("saving sends the draft with the version it started from", async () => {
  renderEditor(3);
  fireEvent.change(bodyBox(), { target: { value: "my edit" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(navigated).toEqual(["/s/trip/p/packing.md"]));
  expect(lastSave()!.body).toMatchObject({
    body: "my edit",
    title: "Packing",
    expected_version: 3,
  });
});

test("tags are split and trimmed, and blanks dropped", async () => {
  renderEditor();
  fireEvent.change(screen.getByLabelText("Tags"), {
    target: { value: " gear , , packing " },
  });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(lastSave()).toBeDefined());
  expect(lastSave()!.body).toMatchObject({ tags: ["gear", "packing"] });
});

test("a conflict keeps the author's text and shows theirs beside it", async () => {
  // The whole point: losing the draft here would lose real writing.
  saveResponse = () => {
    throw new FakeApiError(409, "version_conflict");
  };
  renderEditor(3);
  fireEvent.change(bodyBox(), { target: { value: "my unsaved paragraph" } });
  fireEvent.click(screen.getByText("Save"));

  await waitFor(() => expect(screen.getByText(/Someone saved this page/)).toBeDefined());
  expect(bodyBox().value).toBe("my unsaved paragraph");
  expect(screen.getByText(/now v7/)).toBeDefined();
  // Their version is shown for comparison, not merged in.
  expect(screen.getByText("their text")).toBeDefined();
  expect(navigated).toEqual([]);
});

test("after a conflict the next save carries the new version", async () => {
  // Without this the author is stuck: every retry conflicts again.
  let attempt = 0;
  saveResponse = () => {
    attempt += 1;
    if (attempt === 1) throw new FakeApiError(409, "version_conflict");
    return {};
  };
  renderEditor(3);
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(screen.getByText(/Someone saved this page/)).toBeDefined());

  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(navigated).toEqual(["/s/trip/p/packing.md"]));
  expect(lastSave()!.body).toMatchObject({ expected_version: 7 });
});

test("a conflict whose latest version cannot be fetched says so", async () => {
  saveResponse = () => {
    throw new FakeApiError(409, "version_conflict");
  };
  getResponse = () => {
    throw new Error("offline");
  };
  renderEditor();
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(
      screen.getByText(/latest version could not be fetched/),
    ).toBeDefined(),
  );
});

test("a refusal that is not a conflict is shown with its reason", async () => {
  saveResponse = () => {
    throw new FakeApiError(400, "page_too_large", "a page may be at most 200000 characters");
  };
  renderEditor();
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(
      screen.getByText("a page may be at most 200000 characters"),
    ).toBeDefined(),
  );
});
