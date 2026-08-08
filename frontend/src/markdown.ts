/**
 * Render a page body's markdown to sanitized HTML.
 *
 * Page bodies are untrusted author input (any space member can write one),
 * so the pipeline is fixed: markdown-it with raw HTML disabled, a rule
 * override that rewrites relative image sources to the ACL-checked image
 * endpoint, then DOMPurify over the result. Every caller renders the
 * output via `dangerouslySetInnerHTML` on the strength of that sanitize
 * step — it is not optional.
 */

import DOMPurify from "dompurify";
import MarkdownIt from "markdown-it";

/** Render-time context threaded through markdown-it's renderer rules. */
interface RenderEnv {
  space: string;
}

/** Matches an absolute URL (has a scheme, e.g. `https://`) or a protocol-relative one. */
const ABSOLUTE_URL = /^([a-z][a-z0-9+.-]*:|\/\/)/i;

const md: MarkdownIt = new MarkdownIt({ html: false, linkify: true });

const defaultImageRule =
  md.renderer.rules.image ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

// Page bodies reference attachments by their storage key, e.g.
// `![roof](attachments/xyz)`. Those relative sources need to resolve
// against the space-scoped, ACL-checked `/api/images/<space>/<key>` route
// rather than as a bare (and unauthenticated) relative URL; absolute
// `http(s)`/other-scheme URLs are left alone.
md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  if (token) {
    const srcIndex = token.attrIndex("src");
    const attr = srcIndex >= 0 ? token.attrs?.[srcIndex] : undefined;
    if (attr && !ABSOLUTE_URL.test(attr[1])) {
      attr[1] = `/api/images/${(env as RenderEnv).space}/${attr[1]}`;
    }
  }
  return defaultImageRule(tokens, idx, options, env, self);
};

/**
 * Render a page's markdown body to sanitized HTML.
 *
 * :param body: the page's markdown body
 * :param space: the space alias the page belongs to, used to rewrite
 *     relative image sources
 * :returns: sanitized HTML safe to pass to `dangerouslySetInnerHTML`
 */
export function renderMarkdown(body: string, space: string): string {
  const html = md.render(body, { space } satisfies RenderEnv);
  return DOMPurify.sanitize(html);
}
