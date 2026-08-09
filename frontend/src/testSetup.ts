/**
 * `bun test` preload: install DOM globals before any test module loads.
 *
 * `markdown.ts` imports DOMPurify's default export, which auto-detects a
 * global `window` at *module-evaluation* time — a same-file
 * `globalThis.window = ...` inside a test does not work, because ES module
 * imports are hoisted and `dompurify`'s own top-level code would already
 * have run (and concluded "not a browser") before that assignment executed.
 * A `bunfig.toml` `[test] preload` entry runs this file first instead, so
 * the global exists before `markdown.ts` (or `dompurify`) is ever imported.
 *
 * jsdom, not happy-dom: verified directly that DOMPurify's `sanitize`
 * degrades silently under happy-dom (script tags survived, unrelated tags
 * were dropped) but works correctly under jsdom — matching DOMPurify's own
 * upstream test suite, which targets jsdom.
 */

import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
});

// @ts-expect-error test-only global DOM shim for dompurify's auto-detection
globalThis.window = dom.window;
globalThis.document = dom.window.document;
