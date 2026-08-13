import test from "node:test";
import assert from "node:assert/strict";
import { parseArgs } from "../src/args.js";

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
  const parsed = parseArgs(["call", "read_page", '{"space":"personal","path":"index.md"}'], {});
  assert.deepEqual(parsed.command, {
    kind: "call",
    tool: "read_page",
    args: '{"space":"personal","path":"index.md"}',
  });
});

test("call defaults arguments to {}", () => {
  const parsed = parseArgs(["call", "load_index"], {});
  assert.deepEqual(parsed.command, { kind: "call", tool: "load_index", args: "{}" });
});

test("unknown command throws", () => {
  assert.throws(() => parseArgs(["frobnicate"], {}), /unknown command/);
});
