/**
 * Tests the real, exported `renderMarkdown` end-to-end (markdown-it → the
 * traversal-safe image rewrite → DOMPurify), not a re-implementation of its
 * internals. `bunfig.toml`'s `[test] preload` installs jsdom's `window`
 * before this file (or `markdown.ts`, or `dompurify`) is ever imported —
 * see `src/testSetup.ts` for why that has to happen before, not inside, a
 * test file.
 */

import { expect, test } from "bun:test";

import { renderMarkdown } from "./markdown";

test("a markdown table is wrapped in its own keyboard-scrollable region", () => {
  const html = renderMarkdown(
    "| Name | Role |\n| --- | --- |\n| Ana | Gardener |",
    "reef",
  );

  expect(html).toContain(
    '<div class="markdown-table-scroll" tabindex="0"><table>',
  );
  expect(html).toContain("<thead>");
  expect(html).toContain("<tbody>");
  expect(html).toContain("</table></div>");
});

test("a plain relative image src is rewritten under the cove's image endpoint", () => {
  const html = renderMarkdown("![roof](attachments/xyz-123)", "reef");
  expect(html).toContain('src="/api/images/reef/attachments/xyz-123"');
});

test("a relative image src with characters needing escaping is percent-encoded per segment", () => {
  // "#" is left untouched by markdown-it's own link-destination
  // normalization (verified directly), so this demonstrates *our* encoding
  // step specifically, not markdown-it's.
  const html = renderMarkdown("![report](attachments/report#draft.png)", "reef");
  expect(html).toContain('src="/api/images/reef/attachments/report%23draft.png"');
});

test("a single-.. traversal segment is neutralized, not resolved outside the cove", () => {
  const html = renderMarkdown("![x](../secret)", "reef");
  expect(html).toContain('src="/api/images/reef/invalid"');
  expect(html).not.toContain("..");
});

test("a multi-segment .. traversal is neutralized the same way", () => {
  const html = renderMarkdown("![x](../../secret)", "reef");
  expect(html).toContain('src="/api/images/reef/invalid"');
  expect(html).not.toContain("..");
});

test("a leading absolute-path src (no scheme) is neutralized, not passed through raw", () => {
  // An empty leading segment from a bare "/etc/passwd"-style src is exactly
  // as dangerous as "../..": it also escapes the intended
  // "/api/images/<cove>/" prefix once the browser resolves the URL.
  const html = renderMarkdown("![x](/etc/passwd)", "reef");
  expect(html).toContain('src="/api/images/reef/invalid"');
});

test("a raw backslash never survives into the final src", () => {
  // Browsers treat "\\" as a path separator for special schemes (http/https)
  // exactly like "/", so a literal backslash reaching the final src would
  // be exactly as dangerous as an un-encoded "/" — markdown-it's own link
  // destination handling already percent-encodes it before our rewrite
  // rule ever runs (verified directly); this locks in that the final
  // output never regresses that, regardless of which layer did the work.
  const html = renderMarkdown("![x](a\\\\b)", "reef");
  expect(html).not.toMatch(/src="[^"]*\\/);
});

test("an absolute https image src passes through untouched", () => {
  const html = renderMarkdown("![x](https://example.com/pic.png)", "reef");
  expect(html).toContain('src="https://example.com/pic.png"');
});

test("a markdown <script> body renders as inert text, not an executable element", () => {
  const html = renderMarkdown("<script>alert(1)</script>", "reef");
  expect(html).not.toContain("<script");
  expect(html).toContain("&lt;script&gt;");
});

test("a raw onerror-bearing <img> body renders as inert text too", () => {
  const html = renderMarkdown("<img src=x onerror=alert(2)>", "reef");
  // The whole tag is escaped to text, so "onerror=alert" still appears —
  // as inert characters, not a live attribute. What matters is that no
  // real <img ...> element (with or without onerror) is ever produced.
  expect(html).not.toContain("<img ");
  expect(html).toContain("&lt;img src=x onerror=alert(2)&gt;");
});
