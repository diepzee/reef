/**
 * The avatar and the stack of them: what shows when there is no picture,
 * and how a stack reports the people it had no room for.
 *
 * `avatarColor.test.ts` already covers the colour and initial in isolation;
 * what is left, and only reachable by rendering, is which element gets
 * drawn, and whether a clickable stack is usable from the keyboard.
 */

import { afterEach, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { Avatar, AvatarStack } from "./Avatar";

afterEach(cleanup);

test("a picture is drawn as an image labelled with the name", () => {
  render(<Avatar name="Wouter" src="/api/me/avatar?v=10" />);
  const img = screen.getByRole("img") as HTMLImageElement;
  expect(img.getAttribute("src")).toBe("/api/me/avatar?v=10");
  // Both, deliberately: alt carries the name to a screen reader, title to a
  // pointer. Neither substitutes for the other.
  expect(img.getAttribute("alt")).toBe("Wouter");
  expect(img.getAttribute("title")).toBe("Wouter");
});

test("no picture falls back to the initial, not a broken image", () => {
  render(<Avatar name="Wouter" />);
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByTitle("Wouter").textContent).toBe("W");
});

test("an empty name still renders something", () => {
  render(<Avatar name="" />);
  expect(screen.getByTitle("").textContent).toBe("?");
});

test("the small variant is marked as such", () => {
  render(<Avatar name="Wouter" size="sm" />);
  expect(screen.getByTitle("Wouter").className).toContain("avatar-sm");
});

test("a stack shows everyone when they fit", () => {
  render(<AvatarStack people={[{ name: "Ann" }, { name: "Bo" }, { name: "Cy" }]} />);
  for (const name of ["Ann", "Bo", "Cy"])
    expect(screen.getByTitle(name)).toBeDefined();
  expect(screen.queryByText(/^\+/)).toBeNull();
});

test("a stack counts the people it had no room for", () => {
  render(<AvatarStack
      people={["Ann", "Bo", "Cy", "Di", "Ed", "Fi"].map((name) => ({ name }))}
      max={4}
    />);
  expect(screen.getByText("+2")).toBeDefined();
  expect(screen.getByTitle("2 more")).toBeDefined();
  // The overflowed people are not drawn, only counted.
  expect(screen.queryByTitle("Ed")).toBeNull();
});

test("a clickable stack opens from the keyboard, not only the mouse", () => {
  // The members sheet is reachable from this stack, so a keyboard user who
  // cannot open it cannot see who is in a cove at all.
  let opened = 0;
  render(<AvatarStack people={[{ name: "Ann" }]} onClick={() => (opened += 1)} />);
  const stack = screen.getByRole("button");
  fireEvent.keyDown(stack, { key: "Enter" });
  fireEvent.keyDown(stack, { key: " " });
  fireEvent.click(stack);
  expect(opened).toBe(3);
  expect(stack.getAttribute("tabIndex")).toBe("0");
});

test("a stack nobody can click is not announced as a button", () => {
  render(<AvatarStack people={[{ name: "Ann" }]} />);
  expect(screen.queryByRole("button")).toBeNull();
});

test("a stack draws each person's picture, and initials for those without", () => {
  // The stack used to take names alone, so it could not carry a picture even
  // when one existed: every member but yourself showed as a coloured initial.
  render(
    <AvatarStack
      people={[
        { name: "Ann", src: "/api/spaces/team/members/a1/avatar?v=9" },
        { name: "Bo", src: null },
      ]}
    />,
  );
  const drawn = screen.getByTitle("Ann") as HTMLImageElement;
  expect(drawn.tagName).toBe("IMG");
  expect(drawn.getAttribute("src")).toBe("/api/spaces/team/members/a1/avatar?v=9");
  // Bo has chosen no picture, so their initial stands in rather than a
  // request that would only 404.
  expect(screen.getByTitle("Bo").tagName).not.toBe("IMG");
  expect(screen.getByTitle("Bo").textContent).toBe("B");
});

test("two people sharing a display name are both drawn", () => {
  // Keyed by name, the second would have collided with the first and React
  // would have rendered one of them.
  render(<AvatarStack people={[{ name: "Sam" }, { name: "Sam" }]} />);
  expect(screen.getAllByTitle("Sam").length).toBe(2);
});

test("a clickable stack carries the label it was given", () => {
  render(
    <AvatarStack
      people={[{ name: "Ann" }]}
      onClick={() => {}}
      ariaLabel="Cove members"
    />,
  );
  expect(screen.getByLabelText("Cove members")).toBeDefined();
});
