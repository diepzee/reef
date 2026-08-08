import { expect, test } from "bun:test";
import { avatarColor, initialOf } from "./avatarColor";

test("deterministic per name", () => {
  expect(avatarColor("Wouter")).toBe(avatarColor("Wouter"));
});

test("stays inside the palette", () => {
  for (const n of ["Demo", "Wouter", "Roos", "張三", ""])
    expect(avatarColor(n)).toMatch(/^#[0-9a-f]{6}$/);
});

test("initial is first grapheme uppercased, ? for empty", () => {
  expect(initialOf("wouter")).toBe("W");
  expect(initialOf("")).toBe("?");
});
