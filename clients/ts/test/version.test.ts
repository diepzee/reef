import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { packageVersion } from "../src/version.js";

/** The folder that holds this package's package.json, from the compiled test. */
function packageRoot(): string {
  let dir = dirname(fileURLToPath(import.meta.url));
  while (!existsSync(join(dir, "tsconfig.json"))) dir = dirname(dir);
  return dir;
}

test("the client reports the version in its package.json", () => {
  const manifest = JSON.parse(readFileSync(join(packageRoot(), "package.json"), "utf8"));
  assert.equal(manifest.name, "@haai/reef-cli");
  assert.equal(packageVersion(), manifest.version);
  assert.notEqual(packageVersion(), "0.1.0");
});

test("a folder outside the package has no version to report", () => {
  assert.throws(() => packageVersion("/"), /package\.json/);
});
