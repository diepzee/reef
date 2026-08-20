/**
 * The roster sheet: who can see a cove, and who may change that.
 *
 * Two things here are privacy boundaries rather than styling. Only an owner
 * may invite or remove, so a non-owner must not be shown those controls at
 * all. And an invite returns a disclosure — what the invited person will be
 * able to read — which has to reach the screen, because it is the moment
 * the inviter learns what they are handing over.
 *
 * The sheet also stays mounted across a close, so its transient state has
 * to be cleared by hand; a stale disclosure reappearing later would report
 * an invite that did not just happen.
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

import { AppearanceContext } from "../useAppearance";
import { MeContext } from "../useMe";

/** The signed-in person, so the roster can mark which row is theirs. */
const ME = {
  person_id: "11111111-1111-4111-8111-111111111111",
  email: "own@example.com",
  display_name: "Ada",
  avatar: null,
};

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
let respond: () => unknown = () => ({ disclosure: "" });
let members: unknown = null;
let navigated: string[] = [];

// Every mock of `../api` must present its whole surface — see the note in
// src/views/NewPage.test.tsx.
mock.module("../api", () => ({
  ApiError: FakeApiError,
  apiGet: () => Promise.resolve({}),
  apiSend: (method: string, path: string, body?: unknown) => {
    sent.push({ method, path, body });
    return Promise.resolve(respond());
  },
  apiDownload: () => Promise.resolve(),
}));

mock.module("../useMembers", () => ({
  useMembers: () => ({
    members,
    error: null,
    refresh: () => Promise.resolve(),
  }),
}));
mock.module("../IndexProvider", () => ({
  useIndex: () => ({ refresh: () => Promise.resolve() }),
}));
mock.module("../useMediaQuery", () => ({ useMediaQuery: () => true }));
mock.module("react-router-dom", () => ({
  ...require("react-router-dom"),
  useNavigate: () => (to: string) => navigated.push(to),
}));

const { MembersSheet } = await import("./MembersSheet");

/** Render the sheet, open unless told otherwise. */
function renderSheet(open = true, onClose = () => {}) {
  return render(
    // The sheet wears the cove's creature and carries the look picker, both
    // of which read the viewer's appearance choices; `me` is what tells the
    // roster which row belongs to the reader.
    <MeContext.Provider value={{ me: ME as never, setAvatar: () => {} }}>
      <AppearanceContext.Provider
        value={{ appearance: {} as never, setAppearance: () => {} }}
      >
        <MemoryRouter>
          <MembersSheet cove="trip" open={open} onClose={onClose} />
        </MemoryRouter>
      </AppearanceContext.Provider>
    </MeContext.Provider>,
  );
}

/**
 * A roster where the viewer is or is not the owner.
 *
 * `person_id` is what each row is keyed by: `email` is blanked for a
 * non-owner, so it collides across every row in that mode.
 */
function roster(is_owner: boolean) {
  return {
    is_owner,
    owner_email: is_owner ? "own@example.com" : "",
    members: [
      {
        person_id: ME.person_id,
        display_name: "Ada",
        email: is_owner ? "own@example.com" : "",
        avatar: null,
      },
      {
        person_id: "22222222-2222-4222-8222-222222222222",
        display_name: "Guest",
        email: is_owner ? "guest@example.com" : "",
        avatar: null,
      },
    ],
  };
}

/** A roster of `n` people, owned by this person unless said otherwise. */
function rosterOf(n: number, is_owner = true) {
  return {
    is_owner,
    owner_email: is_owner ? "own@example.com" : "",
    members: Array.from({ length: n }, (_, i) => ({
      person_id: i === 0 ? ME.person_id : `0000000${i}-0000-4000-8000-00000000000${i}`,
      display_name: i === 0 ? "Ada" : `P${i}`,
      email: is_owner ? (i === 0 ? "own@example.com" : `p${i}@example.com`) : "",
      avatar: null,
    })),
  };
}

beforeEach(() => {
  sent = [];
  navigated = [];
  respond = () => ({ disclosure: "" });
  members = roster(true);
});

afterEach(cleanup);

test("everyone in the cove is listed", () => {
  renderSheet();
  expect(screen.getByText("Ada")).toBeDefined();
  expect(screen.getByText("Guest")).toBeDefined();
});

test("a member with a picture is shown it, not their initial", () => {
  // The complaint this fixes: every face here was a coloured initial,
  // because the roster carried no picture and no endpoint could serve one.
  members = {
    ...roster(true),
    members: [
      {
        person_id: "33333333-3333-4333-8333-333333333333",
        display_name: "Ada",
        email: "own@example.com",
        avatar: "/api/coves/trip/members/33333333-3333-4333-8333-333333333333/avatar?v=7",
      },
      {
        person_id: "44444444-4444-4444-8444-444444444444",
        display_name: "Guest",
        email: "guest@example.com",
        avatar: null,
      },
    ],
  };
  renderSheet();
  const face = screen.getByTitle("Ada") as HTMLImageElement;
  expect(face.tagName).toBe("IMG");
  expect(face.getAttribute("src")).toContain("/avatar?v=7");
  expect(screen.getByTitle("Guest").tagName).not.toBe("IMG");
});

test("the cove's own look is changed from here", () => {
  // It used to sit loose in CoveView's body, between "New page" and the
  // delete zone, where it read as a property of the cove rather than of the
  // viewer. Name, colour and creature now share one section, because all
  // three change this cove for you and for nobody else in it.
  renderSheet();
  expect(screen.getByText("Appearance")).toBeDefined();
  expect(screen.getByText("Name")).toBeDefined();
  expect(screen.getByText("Colour")).toBeDefined();
  expect(screen.getByText("Icon")).toBeDefined();
  expect(screen.getByText("Rename for me")).toBeDefined();
});

test("the appearance section says the choice is yours alone, once", () => {
  // The picker no longer says it itself — a per-person setting that looked
  // shared would stop people using it, but said twice under one heading it
  // read as a warning.
  renderSheet();
  expect(screen.getAllByText(/Only you\./).length).toBe(1);
});

test("a non-owner may still restyle the cove for themselves", () => {
  // The one control in this sheet a non-owner can actually use — hiding it
  // with the owner-only controls would strand it entirely for them.
  members = roster(false);
  renderSheet();
  expect(screen.getByText("Appearance")).toBeDefined();
  expect(screen.queryByText("Invite a person")).toBeNull();
});

test("the reader's own row is marked, even when every address is blanked", () => {
  // For a non-owner this chip is the only thing on the roster that says
  // where they are in it.
  members = roster(false);
  renderSheet();
  const you = document.querySelectorAll(".mbs-you-tag");
  expect(you.length).toBe(1);
  expect(you[0]!.closest(".mbs-person")!.textContent).toContain("Ada");
});

test("an owner sees who owns it and can remove the others", () => {
  renderSheet();
  // The owner's own row is tagged, and carries no Remove: exactly one
  // Remove control exists, and it belongs to the other person.
  expect(document.querySelectorAll(".mbs-owner-tag").length).toBe(1);
  expect(screen.getAllByText("Remove…").length).toBe(1);
});

test("a non-owner is offered no way to invite or remove anyone", () => {
  // The API refuses these anyway; showing them would promise something the
  // person cannot do, and leak who is privileged.
  members = roster(false);
  renderSheet();
  expect(screen.queryByText("Remove…")).toBeNull();
  // And with every email blanked, nobody is wrongly tagged as the owner.
  expect(document.querySelectorAll(".mbs-owner-tag").length).toBe(0);
  expect(screen.queryByLabelText(/Email/i)).toBeNull();
});

test("removing asks before it acts", () => {
  renderSheet();
  fireEvent.click(screen.getByText("Remove…"));
  expect(sent).toEqual([]);
  expect(screen.getByText("Confirm remove")).toBeDefined();
});

test("confirming a removal sends it for that person only", async () => {
  renderSheet();
  fireEvent.click(screen.getByText("Remove…"));
  fireEvent.click(screen.getByText("Confirm remove"));
  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toMatchObject({
    method: "DELETE",
    path: "/api/coves/trip/members/guest%40example.com",
  });
});

test("cancelling a removal sends nothing", () => {
  renderSheet();
  fireEvent.click(screen.getByText("Remove…"));
  fireEvent.click(screen.getByText("Cancel"));
  expect(sent).toEqual([]);
  expect(screen.getByText("Remove…")).toBeDefined();
});

test("a failed removal is reported rather than silently ignored", async () => {
  respond = () => {
    throw new FakeApiError(403, "not_allowed");
  };
  renderSheet();
  fireEvent.click(screen.getByText("Remove…"));
  fireEvent.click(screen.getByText("Confirm remove"));
  await waitFor(() => expect(screen.getByText("not_allowed")).toBeDefined());
});

test("Escape closes the sheet", () => {
  let closed = 0;
  renderSheet(true, () => (closed += 1));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(closed).toBe(1);
});

test("Escape does nothing while the sheet is shut", () => {
  // The listener is only wired while open, so it cannot shadow Escape
  // elsewhere in the app.
  let closed = 0;
  renderSheet(false, () => (closed += 1));
  fireEvent.keyDown(document, { key: "Escape" });
  expect(closed).toBe(0);
});

/*
 * The way out of the cove, moved here from CoveView's body. Which act is on
 * offer depends on who else is in it, and the guards in front of each are the
 * only thing between a misclick and a cove nobody can get back.
 */

test("with others here, the exit is leaving — never deleting", () => {
  members = rosterOf(3);
  renderSheet();
  expect(screen.getByText("Leave this cove")).toBeDefined();
  expect(screen.queryByText("Delete this cove…")).toBeNull();
});

test("an owner leaving is told the cove survives without them", () => {
  members = rosterOf(3);
  renderSheet();
  expect(screen.getByText(/Ownership passes to another member/)).toBeDefined();
  expect(screen.getByText(/2 other people/)).toBeDefined();
});

test("a member leaving is told it stays for everyone else", () => {
  members = rosterOf(3, false);
  renderSheet();
  expect(screen.getByText(/It stays for everyone else/)).toBeDefined();
});

test("leaving asks once, then posts", async () => {
  members = rosterOf(3);
  renderSheet();
  fireEvent.click(screen.getByText("Leave this cove"));
  expect(sent).toEqual([]);
  fireEvent.click(screen.getByText("Confirm — leave trip"));
  await waitFor(() => expect(navigated).toEqual(["/"]));
  expect(sent).toEqual([
    { method: "POST", path: "/api/coves/trip/leave", body: undefined },
  ]);
});

test("alone in a cove, the exit is deletion and says what goes with it", () => {
  members = rosterOf(1);
  renderSheet();
  expect(screen.getByText("Delete this cove…")).toBeDefined();
  expect(
    screen.getByText(/pages, files, and history go with it, permanently/),
  ).toBeDefined();
  expect(screen.queryByText("Leave this cove")).toBeNull();
});

test("deleting needs the cove's own name typed", () => {
  members = rosterOf(1);
  renderSheet();
  fireEvent.click(screen.getByText("Delete this cove…"));
  const confirm = screen.getByText("Permanently delete trip") as HTMLButtonElement;
  expect(confirm.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText(/Type/), { target: { value: "trp" } });
  expect(confirm.disabled).toBe(true);
});

test("the typed name unlocks deletion, and it sends the confirmation", async () => {
  members = rosterOf(1);
  renderSheet();
  fireEvent.click(screen.getByText("Delete this cove…"));
  fireEvent.change(screen.getByLabelText(/Type/), { target: { value: "trip" } });
  fireEvent.click(screen.getByText("Permanently delete trip"));
  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toMatchObject({
    method: "DELETE",
    path: "/api/coves/trip",
    body: { confirmation: "trip" },
  });
});

test("a refused exit is reported and the reader stays put", async () => {
  members = rosterOf(3);
  respond = () => {
    throw new FakeApiError(403, "not_allowed");
  };
  renderSheet();
  fireEvent.click(screen.getByText("Leave this cove"));
  fireEvent.click(screen.getByText("Confirm — leave trip"));
  await waitFor(() => expect(screen.getByText("not_allowed")).toBeDefined());
  expect(navigated).toEqual([]);
});

test("a half-armed deletion does not survive closing the sheet", () => {
  // The sheet stays mounted across a close, so nothing clears this on its
  // own — and reopening to find "Permanently delete" already revealed would
  // be an invitation nobody asked for twice.
  members = rosterOf(1);
  const { rerender } = renderSheet();
  fireEvent.click(screen.getByText("Delete this cove…"));
  expect(screen.getByText("Permanently delete trip")).toBeDefined();

  rerender(
    <MeContext.Provider value={{ me: ME as never, setAvatar: () => {} }}>
      <AppearanceContext.Provider
        value={{ appearance: {} as never, setAppearance: () => {} }}
      >
        <MemoryRouter>
          <MembersSheet cove="trip" open={false} onClose={() => {}} />
        </MemoryRouter>
      </AppearanceContext.Provider>
    </MeContext.Provider>,
  );
  expect(screen.queryByText("Permanently delete trip")).toBeNull();
  expect(screen.getByText("Delete this cove…")).toBeDefined();
});
