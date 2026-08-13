/**
 * Choosing how to encode an avatar so the endpoint will actually take it.
 *
 * The endpoint caps a stored picture at 512kB (`AVATAR_MAX_BYTES` in
 * `src/rif/web/routes_api.py`). Downscaling to a 512px square is not on its
 * own enough to stay under that: a 512px square of a *photograph* — foliage,
 * skin, hair, anything without flat areas — is around 750kB as PNG. So the
 * encoder has to be chosen by the size it actually produced, not assumed.
 *
 * Two things vary by browser and neither can be feature-detected up front:
 * whether `toDataURL` honours the type it was asked for (it silently returns
 * PNG when it cannot encode WebP), and how large the result is. Both are
 * answered by encoding and looking, which is what `encodeWithinLimit` does.
 *
 * The canvas work stays in `Profile.tsx`; what lives here is the part that
 * can be tested without a real canvas.
 */

/** Ceiling on a stored avatar; mirrors ``AVATAR_MAX_BYTES`` on the server. */
export const MAX_BYTES = 512_000;

/**
 * Encodings to try, best first. WebP is smallest at a given quality but is
 * not universally encodable; JPEG is the one format every canvas can write,
 * and at these dimensions lands far enough under the ceiling that the last
 * attempt is a formality rather than a real fallback.
 */
export const ATTEMPTS: ReadonlyArray<{ type: string; quality: number }> = [
  { type: "image/webp", quality: 0.9 },
  { type: "image/jpeg", quality: 0.85 },
  { type: "image/jpeg", quality: 0.6 },
];

/**
 * Types the endpoint stores; mirrors ``AVATAR_MIMES`` on the server.
 *
 * What a browser returns is not always what it was asked for — a canvas that
 * cannot write WebP answers with PNG instead. That substitution is fine as
 * long as the result is a type the server takes and small enough, so what is
 * checked is the encoding that came back, never the one that was requested.
 */
export const ALLOWED = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
]);

/** A picture encoded and ready for the API. */
export interface Encoded {
  mime: string;
  data: string;
}

/**
 * Split a base64 data URL into its mime type and payload.
 *
 * :param url: a data URL, e.g. ``"data:image/webp;base64,UklGR…"``
 * :returns: the parts, or null if this is not a base64 data URL — which is
 *     what a zero-area canvas yields (the literal string ``"data:,"``), and
 *     silently sending its empty payload is a guaranteed 400
 */
export function parseDataUrl(url: string): Encoded | null {
  const match = /^data:([^;,]+);base64,(.+)$/s.exec(url);
  if (!match?.[1] || !match[2]) return null;
  return { mime: match[1], data: match[2] };
}

/**
 * Return how many bytes a base64 payload decodes to.
 *
 * Counted rather than decoded: this runs on strings up to a megabyte, and
 * the server's ceiling applies to the decoded length, not the encoded one.
 *
 * :param data: the base64 payload, without the data-URL header
 * :returns: the decoded length in bytes
 */
export function base64Bytes(data: string): number {
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return Math.floor((data.length * 3) / 4) - padding;
}

/** Raised when no attempted encoding produced something small enough. */
export class TooLarge extends Error {}

/**
 * Encode with the first attempt that the server will take and that fits.
 *
 * An attempt is discarded when it produced nothing usable, a type the
 * endpoint does not store, or more than `maxBytes`. The type that came back
 * is what counts — a browser substituting PNG for WebP has still produced
 * something perfectly storable, provided it is small enough.
 *
 * :param encode: renders the canvas, e.g. ``canvas.toDataURL``
 * :param maxBytes: the ceiling to stay under
 * :raises TooLarge: if every attempt was unusable or oversized
 * :returns: the first encoding that fits
 */
export function encodeWithinLimit(
  encode: (type: string, quality: number) => string,
  maxBytes: number = MAX_BYTES,
): Encoded {
  let smallest = Infinity;
  for (const attempt of ATTEMPTS) {
    const parsed = parseDataUrl(encode(attempt.type, attempt.quality));
    if (!parsed || !ALLOWED.has(parsed.mime)) continue;
    const bytes = base64Bytes(parsed.data);
    if (bytes <= maxBytes) return parsed;
    smallest = Math.min(smallest, bytes);
  }
  throw new TooLarge(
    smallest === Infinity
      ? "that picture could not be encoded"
      : `that picture is still ${Math.round(smallest / 1000)}kB once resized, ` +
        `over the ${Math.round(maxBytes / 1000)}kB limit`,
  );
}
