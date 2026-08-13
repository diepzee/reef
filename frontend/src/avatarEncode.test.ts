import { expect, test } from "bun:test";

import {
  base64Bytes,
  encodeWithinLimit,
  MAX_BYTES,
  parseDataUrl,
  TooLarge,
} from "./avatarEncode";

/** Build a base64 data URL whose payload decodes to `bytes` bytes. */
function dataUrl(type: string, bytes: number): string {
  return `data:${type};base64,${"A".repeat(Math.ceil(bytes / 3) * 4)}`;
}

test("parses a base64 data URL into its parts", () => {
  expect(parseDataUrl("data:image/webp;base64,UklGRg==")).toEqual({
    mime: "image/webp",
    data: "UklGRg==",
  });
});

test("a zero-area canvas yields no encoding, not an empty one", () => {
  // A 0x0 canvas returns exactly this, and the old code turned it into a
  // request with an empty mime and empty data — a guaranteed 400.
  expect(parseDataUrl("data:,")).toBeNull();
});

test("counts decoded bytes, allowing for padding", () => {
  expect(base64Bytes("QQ==")).toBe(1);
  expect(base64Bytes("QUJD")).toBe(3);
});

test("takes the first encoding that fits", () => {
  const asked: string[] = [];
  const chosen = encodeWithinLimit((type) => {
    asked.push(type);
    return dataUrl(type, 40_000);
  });
  expect(chosen.mime).toBe("image/webp");
  expect(asked).toEqual(["image/webp"]);
});

test("keeps a substituted type when it is storable and small enough", () => {
  // A canvas without WebP encoding answers a WebP request with PNG. That
  // PNG is perfectly storable, so it is sent rather than re-encoded.
  const chosen = encodeWithinLimit((type) =>
    type === "image/webp"
      ? dataUrl("image/png", 40_000)
      : dataUrl(type, 40_000),
  );
  expect(chosen.mime).toBe("image/png");
});

test("a browser that only ever writes PNG still gets a usable encoding", () => {
  // The last resort: every request answered with PNG, the big one refused
  // on size, so what is sent is the smallest PNG offered.
  const chosen = encodeWithinLimit((_type, quality) =>
    dataUrl("image/png", quality > 0.8 ? 766_914 : 300_000),
  );
  expect(chosen.mime).toBe("image/png");
  expect(base64Bytes(chosen.data)).toBeLessThanOrEqual(MAX_BYTES);
});

test("refuses a type the endpoint would not store", () => {
  expect(() =>
    encodeWithinLimit(() => dataUrl("image/svg+xml", 1_000)),
  ).toThrow(TooLarge);
});

test("falls past an encoding that is over the ceiling", () => {
  // The reported bug: a 512px square photo is ~750kB as PNG. Accepting the
  // first encoding regardless of size is what produced the 400.
  const chosen = encodeWithinLimit((type, quality) =>
    type === "image/webp"
      ? dataUrl("image/png", 766_914)
      : dataUrl(type, quality > 0.8 ? 600_000 : 30_000),
  );
  expect(chosen.mime).toBe("image/jpeg");
  expect(base64Bytes(chosen.data)).toBeLessThanOrEqual(MAX_BYTES);
});

test("says how far over it is when nothing fits", () => {
  expect(() => encodeWithinLimit(() => dataUrl("image/webp", 900_000))).toThrow(
    TooLarge,
  );
  try {
    encodeWithinLimit((type) => dataUrl(type, 900_000));
  } catch (error) {
    expect((error as Error).message).toContain("900kB");
    expect((error as Error).message).toContain("512kB");
  }
});
