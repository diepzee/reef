import test from "node:test";
import assert from "node:assert/strict";
import { parseArgs, USAGE } from "../src/args.js";

test("defaults", () => {
  const parsed = parseArgs(["tools"], {});
  assert.equal(parsed.url, "https://reefwith.me/mcp");
  assert.equal(parsed.compact, false);
  assert.deepEqual(parsed.command, { kind: "tools" });
});

test("env url override and --compact", () => {
  const parsed = parseArgs(["--compact", "login"], { REEF_MCP_URL: "https://x.test/mcp" });
  assert.equal(parsed.url, "https://x.test/mcp");
  assert.equal(parsed.compact, true);
  assert.deepEqual(parsed.command, { kind: "login" });
});

test("call with inline json", () => {
  const parsed = parseArgs(["call", "read_page", '{"cove":"personal","path":"index.md"}'], {});
  assert.deepEqual(parsed.command, {
    kind: "call",
    tool: "read_page",
    args: '{"cove":"personal","path":"index.md"}',
  });
});

test("call defaults arguments to {}", () => {
  const parsed = parseArgs(["call", "load_index"], {});
  assert.deepEqual(parsed.command, { kind: "call", tool: "load_index", args: "{}" });
});

test("unknown command throws", () => {
  assert.throws(() => parseArgs(["frobnicate"], {}), /unknown command/);
});

test("--help short-circuits even mid-command, e.g. `reef call --help`", () => {
  assert.throws(
    () => parseArgs(["call", "--help"], {}),
    (err: unknown) => err instanceof Error && err.message === USAGE,
  );
});
