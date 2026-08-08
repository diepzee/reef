import { expect, test } from "bun:test";
import { indexDescription } from "./summary";

test("first prose line wins", () => {
  expect(indexDescription("# Title\n\nThe summary line.\nMore.")).toBe(
    "The summary line.",
  );
});

test("headings and blanks are skipped", () => {
  expect(indexDescription("# A\n## B\n\n")).toBe("");
});

test("trimmed to 200 chars", () => {
  expect(indexDescription("x".repeat(300)).length).toBe(200);
});
