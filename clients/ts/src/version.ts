/** The version this client reports to the server: the one in its own package.json. */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE_NAME = "@haai/reef-cli";

/**
 * Read this package's version from its package.json.
 *
 * The file sits one folder above the compiled code in the published package
 * and two above it in a test build, so the search walks up from `from` until
 * it finds the package.json that carries this package's name.
 *
 * @param from - the file or folder to start from; this module by default
 * @returns the version, as package.json states it
 * @throws Error when no package.json of this package is found above `from`
 */
export function packageVersion(from: string = fileURLToPath(import.meta.url)): string {
  let dir = dirname(from);
  for (;;) {
    const candidate = join(dir, "package.json");
    if (existsSync(candidate)) {
      const manifest = JSON.parse(readFileSync(candidate, "utf8")) as { name?: string; version?: string };
      if (manifest.name === PACKAGE_NAME && manifest.version) return manifest.version;
    }
    const parent = dirname(dir);
    if (parent === dir) throw new Error(`no package.json of ${PACKAGE_NAME} above ${from}`);
    dir = parent;
  }
}
