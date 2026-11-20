import { expect, test } from "bun:test";
import { normalizeEngramPath, engramPathProblem } from "./engramPath";

test(".md is appended when left off", () => {
  expect(normalizeEngramPath("notes/first-day")).toBe("notes/first-day.md");
});

test("an existing .md is not doubled", () => {
  expect(normalizeEngramPath("notes/first-day.md")).toBe("notes/first-day.md");
});

test("case and stray whitespace are fixed, not refused", () => {
  expect(normalizeEngramPath("  Trip/Packing List  ")).toBe(
    "trip/packing-list.md",
  );
});

test("an empty box normalizes to nothing and reports nothing", () => {
  expect(normalizeEngramPath("   ")).toBe("");
  expect(engramPathProblem("")).toBeNull();
});

test("a normalized path is accepted", () => {
  expect(engramPathProblem(normalizeEngramPath("Trip/Packing List"))).toBeNull();
});

test("meta/ stays reserved", () => {
  expect(engramPathProblem(normalizeEngramPath("meta/persona"))).toContain(
    "reserved",
  );
});

test("empty segments are reported", () => {
  expect(engramPathProblem(normalizeEngramPath("notes//a"))).toContain("empty");
  expect(engramPathProblem(normalizeEngramPath("/notes/a"))).toContain("“/”");
});

test("dot segments are reported", () => {
  expect(engramPathProblem(normalizeEngramPath("../escape"))).toContain("..");
});

test("an unfixable character is named", () => {
  const problem = engramPathProblem(normalizeEngramPath("notes/what?"));
  expect(problem).toContain("“?”");
});

test("a path that is only an extension asks for a name", () => {
  expect(engramPathProblem(normalizeEngramPath("notes/"))).toContain("name");
  expect(engramPathProblem(normalizeEngramPath(".md"))).toContain("name");
});
