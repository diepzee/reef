/**
 * Renders public/reef.svg to the raster icon sizes the backend serves
 * (src/reef/web/static.py: reef-icon.png doubles as /favicon.ico and
 * /apple-touch-icon.png — 180px is the apple-touch convention).
 * Run: bun run scripts/render-icons.ts
 */
import { Resvg } from "@resvg/resvg-js";

const svg = await Bun.file(new URL("../public/reef.svg", import.meta.url)).text();

for (const { out, size } of [{ out: "../public/reef-icon.png", size: 180 }]) {
  const png = new Resvg(svg, { fitTo: { mode: "width", value: size } }).render().asPng();
  await Bun.write(new URL(out, import.meta.url), png);
  console.log(`${out}: ${png.length} bytes at ${size}px`);
}
