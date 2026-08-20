/**
 * Render a page body's markdown to sanitized HTML.
 *
 * Page bodies are untrusted author input (any cove member can write one),
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
  cove: string;
}

/** Matches an absolute URL (has a scheme, e.g. `https://`) or a protocol-relative one. */
const ABSOLUTE_URL = /^([a-z][a-z0-9+.-]*:|\/\/)/i;

/**
 * A relative src that resolves outside the cove's image root gets pointed
 * here instead — a key no attachment can ever have, so it 404s rather than
 * ever reaching a page the browser would resolve the traversal against.
 */
const INVALID_IMAGE_SRC = "invalid";

/**
 * Rewrite a markdown image src to the cove-scoped image endpoint.
 *
 * Page bodies reference attachments by their storage key, e.g.
 * `![roof](attachments/xyz)`, and those relative sources need to resolve
 * against the ACL-checked `/api/images/<cove>/<key>` route rather than as
 * a bare (and unauthenticated) relative URL. The rewrite has to defend
 * against path traversal, though: naively concatenating an untrusted
 * relative src (`../../secret`) would let a page author smuggle a
 * cookie-authenticated request to an arbitrary same-origin path past the
 * intended `/api/images/<cove>/` prefix, once every viewer's browser
 * resolves the `..` segments. Every path segment is therefore validated
 * (no empty/`.`/`..` segment survives) and percent-encoded before being
 * rejoined, so no character in a key — including a literal `/` or `\` —
 * can be reinterpreted as a path separator by the browser's own URL
 * resolution.
 *
 * :param src: the image src exactly as written in the markdown body
 * :param cove: the cove alias the page belongs to
 * :returns: `src` untouched if absolute; otherwise the rewritten,
 *     traversal-safe `/api/images/<cove>/<key>` URL
 */
function resolveImageSrc(src: string, cove: string): string {
  if (ABSOLUTE_URL.test(src)) {
    return src;
  }
  const segments = src.split("/");
  const hasTraversal = segments.some(
    (segment) => segment === "" || segment === "." || segment === "..",
  );
  const key = hasTraversal
    ? INVALID_IMAGE_SRC
    : segments.map(encodeURIComponent).join("/");
  return `/api/images/${encodeURIComponent(cove)}/${key}`;
}

const md: MarkdownIt = new MarkdownIt({ html: false, linkify: true });

/*
 * A table can be wider than the reading column, especially on a phone.
 * Keep that overflow local to the table instead of making the whole page
 * scroll sideways. The wrapper class is also shared by PageView and the
 * editor preview, so both surfaces get exactly the same treatment.
 */
md.renderer.rules.table_open = () =>
  '<div class="markdown-table-scroll" tabindex="0"><table>\n';
md.renderer.rules.table_close = () => "</table></div>\n";

const defaultImageRule =
  md.renderer.rules.image ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  if (token) {
    const srcIndex = token.attrIndex("src");
    const attr = srcIndex >= 0 ? token.attrs?.[srcIndex] : undefined;
    if (attr) {
      attr[1] = resolveImageSrc(attr[1], (env as RenderEnv).cove);
    }
  }
  return defaultImageRule(tokens, idx, options, env, self);
};

/**
 * Render a page's markdown body to sanitized HTML.
 *
 * :param body: the page's markdown body
 * :param cove: the cove alias the page belongs to, used to rewrite
 *     relative image sources
 * :returns: sanitized HTML safe to pass to `dangerouslySetInnerHTML`
 */
export function renderMarkdown(body: string, cove: string): string {
  const html = md.render(body, { cove } satisfies RenderEnv);
  return DOMPurify.sanitize(html);
}
