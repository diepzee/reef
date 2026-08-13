import { expect, test } from "bun:test";
import { normalizePagePath, pagePathProblem } from "./pagePath";

test(".md is appended when left off", () => {
  expect(normalizePagePath("notes/first-day")).toBe("notes/first-day.md");
});

test("an existing .md is not doubled", () => {
  expect(normalizePagePath("notes/first-day.md")).toBe("notes/first-day.md");
});

test("case and stray whitespace are fixed, not refused", () => {
  expect(normalizePagePath("  Trip/Packing List  ")).toBe(
    "trip/packing-list.md",
  );
});

test("an empty box normalizes to nothing and reports nothing", () => {
  expect(normalizePagePath("   ")).toBe("");
  expect(pagePathProblem("")).toBeNull();
});

test("a normalized path is accepted", () => {
  expect(pagePathProblem(normalizePagePath("Trip/Packing List"))).toBeNull();
});

test("meta/ stays reserved", () => {
  expect(pagePathProblem(normalizePagePath("meta/persona"))).toContain(
    "reserved",
  );
});

test("empty segments are reported", () => {
  expect(pagePathProblem(normalizePagePath("notes//a"))).toContain("empty");
  expect(pagePathProblem(normalizePagePath("/notes/a"))).toContain("“/”");
});

test("dot segments are reported", () => {
  expect(pagePathProblem(normalizePagePath("../escape"))).toContain("..");
});

test("an unfixable character is named", () => {
  const problem = pagePathProblem(normalizePagePath("notes/what?"));
  expect(problem).toContain("“?”");
});

test("a path that is only an extension asks for a name", () => {
  expect(pagePathProblem(normalizePagePath("notes/"))).toContain("name");
  expect(pagePathProblem(normalizePagePath(".md"))).toContain("name");
});
