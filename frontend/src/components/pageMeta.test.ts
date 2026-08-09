import { expect, test } from "bun:test";
import { pageMetaSentence } from "./pageMeta";

test("shared space, editor, version", () => {
  const s = pageMetaSentence({
    space: "reef",
    personal: false,
    lastEditor: "Wouter",
    updated: new Date(Date.now() - 7200_000).toISOString(),
    version: 2,
  });
  expect(s).toContain("seen by everyone in reef");
  expect(s).toContain("edited by Wouter");
  expect(s).toContain("v2");
});

test("personal space says only you; null editor omitted", () => {
  const s = pageMetaSentence({
    space: "personal",
    personal: true,
    lastEditor: null,
    updated: new Date().toISOString(),
  });
  expect(s).toContain("only you");
  expect(s).not.toContain("edited by");
});
