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
 *
 * `window` and `document` alone are enough for DOMPurify, but not for
 * rendering a component: React and Testing Library reach for bare
 * `HTMLElement`, `Node`, `Event`, `getComputedStyle` and friends, and a
 * missing one surfaces as a puzzling failure deep inside React rather than
 * as "no DOM here". So every global the jsdom window defines that the test
 * realm does not already have is copied across, once, below.
 */

import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
});

// @ts-expect-error test-only global DOM shim for dompurify's auto-detection
globalThis.window = dom.window;
globalThis.document = dom.window.document;

// Bun's realm already provides some of these (fetch, URL, TextEncoder…) and
// its versions are the ones the app code would meet in a browser, so
// existing globals are left alone and only the gaps are filled.
for (const key of Object.getOwnPropertyNames(dom.window)) {
  if (key in globalThis) continue;
  Object.defineProperty(globalThis, key, {
    // A getter, not a copied value: some of these are lazy on the jsdom
    // window, and reading them eagerly here would instantiate every one.
    get: () => dom.window[key as keyof typeof dom.window],
    configurable: true,
  });
}

// React 19 uses this to decide whether `act` is available, and warns on
// every state update when it is unset.
// @ts-expect-error test-only flag React reads off the global
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// jsdom implements no SVG layout at all, so `getBBox` is simply absent —
// and the cove glyphs measure themselves with it to sit on a common
// baseline. Without this, rendering any view that draws one throws rather
// than failing an assertion, which reads as a broken test rather than a
// missing browser API. Zeroes are honest here: there is no layout to report.
const svgProto = dom.window.SVGElement.prototype as unknown as {
  getBBox?: () => { x: number; y: number; width: number; height: number };
};
svgProto.getBBox ??= () => ({ x: 0, y: 0, width: 0, height: 0 });
