# Marketing Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make reef discoverable and installable by technical hobbyists: publishable Python + TS CLIs named `reefwith`, a landing page that sells the household outcome with the tech proud and visible, repo metadata, registry seed files, and launch drafts.

**Architecture:** The Python CLI moves out of the server package into `clients/python` as a small publishable distribution (`reefwith` on PyPI, command `reef`). A new minimal TS CLI lives in `clients/ts` (`reefwith` on npm, bins `reef`/`reefwith`) exposing only `login`/`logout`/`tools`/`call` — the generic passthrough is the contract, so the two-CLI sync tax stays near zero. Site and README copy update to the new install commands. Registry submission files and launch drafts are committed; irreversible operator actions (repo flip, publishes, posts) live in a checklist for Wouter.

**Tech Stack:** Python 3.13 / fastmcp / uv workspaces / uv_build; TypeScript / Node ≥ 20 / `@modelcontextprotocol/sdk` (Streamable HTTP + OAuth); pytest; `node --test`.

## Global Constraints

- Package name on PyPI **and** npm: `reefwith` (verified available 2026-08-13). Installed command: `reef`.
- Default MCP endpoint everywhere: `https://reefwith.me/mcp`.
- The TS CLI implements ONLY `login`, `logout`, `tools`, `call` — no per-tool sugar.
- Site visual identity untouched: palette, coral mark, scroll structure, dark mode. Copy edits only.
- `main` auto-deploys production (Railway). Do not merge copy advertising `uv tool install reefwith` / `npm install -g reefwith` until both packages are published — sequencing lives in the operator checklist.
- Python: modern types, ReST docstrings without types, docstrings mandatory.
- All user-facing copy follows plain language (ISO 24495-1): reader-first, one idea per sentence, everyday words.

---

### Task 1: Extract the Python CLI into a publishable `reefwith` package

**Files:**
- Create: `clients/python/pyproject.toml`
- Move: `src/rif/cli.py` → `clients/python/src/reefwith/cli.py` (git mv)
- Create: `clients/python/src/reefwith/__init__.py`
- Modify: `pyproject.toml` (root — drop `[project.scripts]`, add workspace)
- Modify: `tests/test_cli.py` (imports only)

**Interfaces:**
- Produces: importable module `reefwith.cli` with unchanged public names `JsonTokenStore`, `build_parser`, `tool_call`, `run`, `main`; console script `reef = "reefwith.cli:main"` now owned by the `reefwith` distribution.

- [ ] **Step 1: Baseline — run the existing CLI tests**

Run: `uv run pytest tests/test_cli.py -q`
Expected: PASS (this is the green baseline the move must preserve).

- [ ] **Step 2: Move the module**

```bash
mkdir -p clients/python/src/reefwith
git mv src/rif/cli.py clients/python/src/reefwith/cli.py
```

- [ ] **Step 3: Create the package files**

`clients/python/src/reefwith/__init__.py`:

```python
"""reefwith — command-line client for Reef's remote MCP server."""
```

`clients/python/pyproject.toml`:

```toml
[project]
name = "reefwith"
version = "0.1.0"
description = "Command-line client for reef — shared, living memory for people and their AI assistants"
readme = "README.md"
authors = [
    { name = "WOUTER DURNEZ", email = "wouter.durnez@gmail.com" }
]
license = "MIT"
requires-python = ">=3.13"
dependencies = [
    "fastmcp>=2",
]
keywords = ["mcp", "memory", "cli", "reef"]

[project.urls]
Homepage = "https://reefwith.me"
Repository = "https://github.com/diepzee/rif"

[project.scripts]
reef = "reefwith.cli:main"

[build-system]
requires = ["uv_build>=0.11.14,<0.12.0"]
build-backend = "uv_build"
```

`clients/python/README.md`:

```markdown
# reefwith

Command-line client for [reef](https://reefwith.me) — shared, living memory
for people and their AI assistants, reached over MCP.

    uv tool install reefwith
    reef login

Every MCP tool is mirrored as a subcommand; `reef call <tool> '<json>'` is the
exact passthrough. JSON on stdout, always.
```

- [ ] **Step 4: Wire the uv workspace in the root `pyproject.toml`**

Remove the root `[project.scripts]` block (`reef = "rif.cli:main"`). Add:

```toml
[tool.uv.workspace]
members = ["clients/python"]

[tool.uv.sources]
reefwith = { workspace = true }
```

Add `"reefwith"` to the `dev` dependency group so `uv sync` installs it editable. In `[tool.pytest.ini_options]`, change `pythonpath = ["src"]` to `pythonpath = ["src", "clients/python/src"]`.

- [ ] **Step 5: Update the test imports**

In `tests/test_cli.py`, replace `from rif.cli import ...` with `from reefwith.cli import ...`, and the `monkeypatch.setattr("rif.cli.time.time", ...)` target with `"reefwith.cli.time.time"`.

- [ ] **Step 6: Sync, run tests, verify the build**

```bash
uv sync
uv run pytest tests/test_cli.py -q
uv run reef --help
uv build clients/python
```

Expected: tests PASS, help prints, `dist/` gains `reefwith-0.1.0` sdist + wheel. Also run the full suite once (`uv run pytest -q`) to catch anything else importing `rif.cli`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Extract the CLI into a publishable reefwith package"
```

---

### Task 2: TypeScript CLI (`clients/ts`, npm `reefwith`)

**Files:**
- Create: `clients/ts/package.json`, `clients/ts/tsconfig.json`, `clients/ts/README.md`
- Create: `clients/ts/src/args.ts`, `clients/ts/src/auth.ts`, `clients/ts/src/index.ts`
- Test: `clients/ts/test/args.test.ts`

**Interfaces:**
- Consumes: the live MCP endpoint `https://reefwith.me/mcp` (OAuth via dynamic client registration, same as the Python CLI).
- Produces: npm package `reefwith` with bins `reef` and `reefwith`; commands `login`, `logout`, `tools`, `call <tool> [json|@file|-]`; flags `--url`, `--compact`; env `REEF_MCP_URL`, `REEF_ACCESS_TOKEN`, `REEF_CONFIG_DIR`.

- [ ] **Step 1: Scaffold the package**

`clients/ts/package.json`:

```json
{
  "name": "reefwith",
  "version": "0.1.0",
  "description": "Command-line client for reef — shared, living memory for people and their AI assistants",
  "keywords": ["mcp", "memory", "cli", "reef"],
  "homepage": "https://reefwith.me",
  "repository": { "type": "git", "url": "git+https://github.com/diepzee/rif.git", "directory": "clients/ts" },
  "license": "MIT",
  "type": "module",
  "bin": { "reef": "dist/index.js", "reefwith": "dist/index.js" },
  "files": ["dist"],
  "engines": { "node": ">=20" },
  "scripts": {
    "build": "tsc -p tsconfig.json",
    "test": "npm run build && node --test dist-test/"
  },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.10.0"
  },
  "devDependencies": {
    "typescript": "^5.6.0",
    "@types/node": "^22.0.0"
  }
}
```

`clients/ts/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "Node16",
    "moduleResolution": "node16",
    "strict": true,
    "declaration": false,
    "outDir": "dist",
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

(The test script compiles tests separately: add a second config `clients/ts/tsconfig.test.json` extending the base with `"include": ["src", "test"], "outDir": "dist-test"`, and make the test script `tsc -p tsconfig.test.json && node --test dist-test/test/`.)

- [ ] **Step 2: Write the failing arg-parser test**

`clients/ts/test/args.test.ts`:

```ts
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
```

- [ ] **Step 3: Run the test to verify it fails**

Run (in `clients/ts/`): `npm install && npm test`
Expected: FAIL — `src/args.js` does not exist.

- [ ] **Step 4: Implement the arg parser**

`clients/ts/src/args.ts`:

```ts
/** Minimal argv parsing for the reef CLI. No dependency needed at this size. */

export type Command =
  | { kind: "login" }
  | { kind: "logout" }
  | { kind: "tools" }
  | { kind: "call"; tool: string; args: string };

export interface Parsed {
  url: string;
  compact: boolean;
  command: Command;
}

export const DEFAULT_MCP_URL = "https://reefwith.me/mcp";

export function parseArgs(argv: string[], env: Record<string, string | undefined>): Parsed {
  let url = env.REEF_MCP_URL ?? DEFAULT_MCP_URL;
  let compact = false;
  const rest: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--url") {
      url = argv[++i] ?? errorOut("--url needs a value");
    } else if (a.startsWith("--url=")) {
      url = a.slice("--url=".length);
    } else if (a === "--compact") {
      compact = true;
    } else if (a === "--help" || a === "-h") {
      rest.push("help");
    } else {
      rest.push(a);
    }
  }
  const [name, ...tail] = rest;
  switch (name) {
    case "login":
    case "logout":
    case "tools":
      return { url, compact, command: { kind: name } };
    case "call": {
      const tool = tail[0] ?? errorOut("call needs a tool name");
      return { url, compact, command: { kind: "call", tool, args: tail[1] ?? "{}" } };
    }
    case "help":
    case undefined:
      throw new UsageError(USAGE);
    default:
      throw new UsageError(`unknown command: ${name}\n${USAGE}`);
  }
}

export class UsageError extends Error {}

function errorOut(message: string): never {
  throw new UsageError(message);
}

export const USAGE = `reef — read and write shared Reef memory over MCP

usage: reef [--url URL] [--compact] <command>

commands:
  login                 sign in through MCP OAuth and cache the tokens
  logout                remove cached OAuth tokens for this endpoint
  tools                 list the MCP server's current tool schemas
  call <tool> [json]    call an exact MCP tool; json may be inline, @file, or '-'

env: REEF_MCP_URL, REEF_ACCESS_TOKEN, REEF_CONFIG_DIR`;
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm test`
Expected: PASS (5 tests).

- [ ] **Step 6: Implement OAuth storage + provider**

`clients/ts/src/auth.ts`:

```ts
/** File-backed OAuth state for the MCP SDK, mirroring the Python CLI's layout. */
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, renameSync, chmodSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { OAuthClientProvider } from "@modelcontextprotocol/sdk/client/auth.js";
import type {
  OAuthClientInformation,
  OAuthClientInformationFull,
  OAuthClientMetadata,
  OAuthTokens,
} from "@modelcontextprotocol/sdk/shared/auth.js";

export function configDir(env: Record<string, string | undefined>): string {
  if (env.REEF_CONFIG_DIR) return env.REEF_CONFIG_DIR;
  if (env.XDG_CONFIG_HOME) return join(env.XDG_CONFIG_HOME, "reef");
  if (process.platform === "win32" && env.APPDATA) return join(env.APPDATA, "reef");
  return join(homedir(), ".config", "reef");
}

type Entry = {
  clientInformation?: OAuthClientInformationFull;
  tokens?: OAuthTokens;
  codeVerifier?: string;
};

/** One JSON file keyed by endpoint URL; user-only permissions; atomic writes. */
export class StateFile {
  constructor(private readonly path: string) {}

  private readAll(): Record<string, Entry> {
    try {
      return JSON.parse(readFileSync(this.path, "utf8"));
    } catch {
      return {};
    }
  }

  get(url: string): Entry {
    return this.readAll()[url] ?? {};
  }

  set(url: string, entry: Entry): void {
    const all = this.readAll();
    all[url] = entry;
    this.writeAll(all);
  }

  delete(url: string): boolean {
    const all = this.readAll();
    const had = url in all;
    if (had) {
      delete all[url];
      this.writeAll(all);
    }
    return had;
  }

  private writeAll(all: Record<string, Entry>): void {
    mkdirSync(join(this.path, ".."), { recursive: true, mode: 0o700 });
    const tmp = this.path + ".tmp";
    writeFileSync(tmp, JSON.stringify(all), { mode: 0o600 });
    if (process.platform !== "win32") chmodSync(tmp, 0o600);
    renameSync(tmp, this.path);
  }
}

/** Provider the SDK drives; also opens the browser and runs the callback server. */
export class CliOAuthProvider implements OAuthClientProvider {
  private port = 0;
  private codePromise?: Promise<string>;
  private resolveCode?: (code: string) => void;
  private server?: ReturnType<typeof createServer>;

  constructor(
    private readonly url: string,
    private readonly store: StateFile,
  ) {}

  get redirectUrl(): string {
    return `http://127.0.0.1:${this.port}/callback`;
  }

  get clientMetadata(): OAuthClientMetadata {
    return {
      client_name: "Reef CLI (node)",
      redirect_uris: [this.redirectUrl],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
    };
  }

  clientInformation(): OAuthClientInformation | undefined {
    return this.store.get(this.url).clientInformation;
  }

  saveClientInformation(info: OAuthClientInformationFull): void {
    this.store.set(this.url, { ...this.store.get(this.url), clientInformation: info });
  }

  tokens(): OAuthTokens | undefined {
    return this.store.get(this.url).tokens;
  }

  saveTokens(tokens: OAuthTokens): void {
    this.store.set(this.url, { ...this.store.get(this.url), tokens });
  }

  saveCodeVerifier(codeVerifier: string): void {
    this.store.set(this.url, { ...this.store.get(this.url), codeVerifier });
  }

  codeVerifier(): string {
    const v = this.store.get(this.url).codeVerifier;
    if (!v) throw new Error("no PKCE verifier saved — run `reef login` again");
    return v;
  }

  /** Start the loopback server; must run before the SDK asks for redirectUrl. */
  async listen(): Promise<void> {
    this.codePromise = new Promise((resolve) => {
      this.resolveCode = resolve;
    });
    this.server = createServer((req, res) => {
      const u = new URL(req.url ?? "/", `http://127.0.0.1:${this.port}`);
      const code = u.searchParams.get("code");
      res.writeHead(200, { "content-type": "text/html" });
      res.end("<p>Signed in — you can close this tab and return to the terminal.</p>");
      if (code) this.resolveCode?.(code);
    });
    await new Promise<void>((resolve) => this.server!.listen(0, "127.0.0.1", resolve));
    const address = this.server.address();
    if (address && typeof address === "object") this.port = address.port;
  }

  redirectToAuthorization(authorizationUrl: URL): void {
    const opener =
      process.platform === "darwin" ? "open" : process.platform === "win32" ? "start" : "xdg-open";
    console.error(`Opening browser to sign in:\n  ${authorizationUrl.href}`);
    spawn(opener, [authorizationUrl.href], { stdio: "ignore", detached: true, shell: process.platform === "win32" }).unref();
  }

  async waitForCode(): Promise<string> {
    if (!this.codePromise) throw new Error("listen() was not called");
    return this.codePromise;
  }

  close(): void {
    this.server?.close();
  }
}
```

- [ ] **Step 7: Implement the entry point**

`clients/ts/src/index.ts`:

```ts
#!/usr/bin/env node
/** reef — thin authenticated client over the remote MCP endpoint. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { UnauthorizedError } from "@modelcontextprotocol/sdk/client/auth.js";
import { parseArgs, UsageError, USAGE } from "./args.js";
import { CliOAuthProvider, StateFile, configDir } from "./auth.js";

function readJsonSource(source: string): unknown {
  let text: string;
  if (source === "-") text = readFileSync(0, "utf8");
  else if (source.startsWith("@")) text = readFileSync(source.slice(1), "utf8");
  else text = source;
  return JSON.parse(text);
}

function printJson(value: unknown, compact: boolean): void {
  console.log(JSON.stringify(value, null, compact ? undefined : 2));
}

async function connect(url: string, provider: CliOAuthProvider | string): Promise<Client> {
  const client = new Client({ name: "reef-cli-node", version: "0.1.0" });
  const options =
    typeof provider === "string"
      ? { requestInit: { headers: { Authorization: `Bearer ${provider}` } } }
      : { authProvider: provider };
  const attempt = async () => {
    const transport = new StreamableHTTPClientTransport(new URL(url), options);
    await client.connect(transport);
    return transport;
  };
  try {
    await attempt();
  } catch (error) {
    if (error instanceof UnauthorizedError && typeof provider !== "string") {
      const code = await provider.waitForCode();
      const transport = new StreamableHTTPClientTransport(new URL(url), options);
      await transport.finishAuth(code);
      provider.close();
      await client.connect(new StreamableHTTPClientTransport(new URL(url), options));
    } else {
      throw error;
    }
  }
  return client;
}

async function main(): Promise<number> {
  const parsed = parseArgs(process.argv.slice(2), process.env);
  const store = new StateFile(join(configDir(process.env), "oauth-node.json"));

  if (parsed.command.kind === "logout") {
    const removed = store.delete(parsed.url);
    printJson({ logged_out: removed, url: parsed.url }, parsed.compact);
    return 0;
  }

  const token = process.env.REEF_ACCESS_TOKEN;
  let provider: CliOAuthProvider | string;
  if (parsed.command.kind === "login" || !token) {
    const oauth = new CliOAuthProvider(parsed.url, store);
    await oauth.listen();
    provider = oauth;
  } else {
    provider = token;
  }

  const client = await connect(parsed.url, provider);
  try {
    let result: unknown;
    if (parsed.command.kind === "login") {
      await client.ping();
      result = { logged_in: true, url: parsed.url };
    } else if (parsed.command.kind === "tools") {
      result = await client.listTools();
    } else {
      const args = readJsonSource(parsed.command.args);
      if (typeof args !== "object" || args === null || Array.isArray(args)) {
        throw new UsageError("call arguments must be a JSON object");
      }
      result = await client.callTool({ name: parsed.command.tool, arguments: args as Record<string, unknown> });
    }
    printJson(result, parsed.compact);
    return typeof result === "object" && result !== null && "isError" in result && (result as { isError?: boolean }).isError ? 1 : 0;
  } finally {
    await client.close();
    if (typeof provider !== "string") provider.close();
  }
}

main().then(
  (status) => process.exit(status),
  (error) => {
    if (error instanceof UsageError) {
      console.error(error.message || USAGE);
      process.exit(2);
    }
    console.error(`reef: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  },
);
```

`clients/ts/README.md`:

```markdown
# reefwith

Command-line client for [reef](https://reefwith.me) — shared, living memory
for people and their AI assistants, reached over MCP.

    npm install -g reefwith
    reef login

`reef call <tool> '<json>'` is the exact MCP passthrough; `reef tools` lists
the live schemas. JSON on stdout, always. (A Python twin exists:
`uv tool install reefwith`.)
```

- [ ] **Step 8: Build, test, and verify against the live server**

```bash
cd clients/ts && npm install && npm test && npm run build
node dist/index.js --help; node dist/index.js tools
```

Expected: tests PASS; `tools` either lists schemas (if a cached/env token exists) or fails with a clear auth error. If the executor has no reef account, verify `login` opens the browser URL printout and stop there — do not create accounts. Check the exact SDK import paths and `OAuthClientProvider` member names against the installed `@modelcontextprotocol/sdk` version and adjust the code to match (the SDK's auth surface has shifted between minors; the structure above is the contract, the SDK's names win).

- [ ] **Step 9: Add `clients/ts/node_modules`, `dist`, `dist-test` to `.gitignore`, commit**

```bash
git add -A
git commit -m "Add a minimal TypeScript CLI published as reefwith"
```

---

### Task 3: Landing page copy restyle (warm story, proud tech)

**Files:**
- Modify: `site/index.html`
- Modify: `tests/test_web_static.py:41-47` (`test_marketing_site_offers_cli_and_agent_skill_setup`)

**Interfaces:**
- Consumes: install commands `uv tool install reefwith` and `npm install -g reefwith` from Tasks 1–2.

- [ ] **Step 1: Update the static-site test to the new expectations**

In `tests/test_web_static.py`, replace the body of `test_marketing_site_offers_cli_and_agent_skill_setup` assertions:

```python
    assert '<option value="cli"' in page
    assert "uv tool install reefwith" in page
    assert "npm install -g reefwith" in page
    assert "reef login" in page
    assert "github.com/diepzee/rif/tree/main/skills/reef" in page
```

Add a second test for the new copy:

```python
def test_marketing_site_tells_the_invite_only_story():
    """The landing page states the door: no sign-up, invitation only."""
    page = (Path(__file__).parents[1] / "site" / "index.html").read_text()
    assert "no sign-up" in page.lower()
```

Run: `uv run pytest tests/test_web_static.py -q` — expected: the two copy tests FAIL (site not yet edited).

- [ ] **Step 2: Concretize the cove scenarios**

In `site/index.html`, directly after the closing `</div>` of `<div class="circles">` (around line 471), add:

```html
  <p class="sub">Inside the household cove: Emma&rsquo;s allergy list, the plumber who
    actually turns up, what you decided about the loft. Written down once —
    known by every conversation after.</p>
```

- [ ] **Step 3: Tell the invite-only story on the landing page**

In the closing lockup section (the final panel with the `reef — memories you grow together` wordmark and the Sign in CTA), add above the Sign in link:

```html
  <p class="sub">There is no sign-up. Someone already on reef invites you &mdash;
    that is the only door, and it is deliberate.</p>
```

- [ ] **Step 4: Update the CLI install copy**

Around `site/index.html:632`, replace:

```html
<code id="reef-cli-install">uv tool install git+https://github.com/diepzee/rif.git</code>
```

with:

```html
<code id="reef-cli-install">uv tool install reefwith</code>
```

and immediately after the uv install step's paragraph, add a Node alternative line in the same step (matching surrounding markup style):

```html
<p>Prefer Node? The same CLI ships on npm:</p>
<p class="cmd"><code>npm install -g reefwith</code> <button class="copy" data-copy="npm install -g reefwith">Copy</button></p>
```

(Adopt whatever copy-button markup pattern the neighbouring steps actually use — mirror the existing `Copy` control exactly rather than inventing a new one.)

- [ ] **Step 5: Plain-language pass**

Read the full visible copy top to bottom once. Fix only sentences that break the reader-first rules (two ideas in one sentence, jargon where an everyday word exists and isn't load-bearing). The current copy is close; expect a handful of touches, not a rewrite. Do not change headings, taglines, or the visual structure.

- [ ] **Step 6: Verify and eyeball**

```bash
uv run pytest tests/test_web_static.py -q
```

Expected: PASS. Then open the page with Playwright (`file://` or via the dev server) at desktop and ~390px mobile width, light and dark, and screenshot the edited sections to confirm nothing overflows or collides.

- [ ] **Step 7: Commit**

```bash
git add site/index.html tests/test_web_static.py
git commit -m "Speak to technical hobbyists on the landing page"
```

---

### Task 4: README, repo metadata, and MCP-registry seed

**Files:**
- Modify: `README.md` (install command near line 50)
- Create: `server.json` (repo root)
- Create: `docs/marketing/repo-metadata.md`

- [ ] **Step 1: Update the README install copy**

Replace `uv tool install .` (README.md:50) with:

```markdown
uv tool install reefwith    # or: npm install -g reefwith
```

and, in the same section, one sentence noting both CLIs share the command name `reef` and the same OAuth login.

- [ ] **Step 2: Create the official-registry manifest**

`server.json` at the repo root (schema per registry.modelcontextprotocol.io; the executor must check the current schema at https://github.com/modelcontextprotocol/registry/tree/main/docs and adjust field names to match it — the registry API froze at v0.1 but the manifest schema evolves):

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-09-29/server.schema.json",
  "name": "me.reefwith/reef",
  "description": "Shared, living memory for you and your people — coves your AI assistant can read and write.",
  "repository": { "url": "https://github.com/diepzee/rif", "source": "github" },
  "version": "0.1.0",
  "remotes": [
    { "type": "streamable-http", "url": "https://reefwith.me/mcp" }
  ]
}
```

- [ ] **Step 3: Prepare repo metadata (safe while private)**

Run now — these are fine on a private repo and survive the flip:

```bash
gh repo edit diepzee/rif \
  --description "reef — shared, living memory for you and your people, over MCP" \
  --homepage "https://reefwith.me"
gh repo edit diepzee/rif --add-topic mcp --add-topic mcp-server --add-topic memory \
  --add-topic claude --add-topic chatgpt --add-topic ai-memory --add-topic model-context-protocol
```

`docs/marketing/repo-metadata.md` records the chosen description, homepage, topics, and notes that the social preview image (1280×640, the coral mark on the seafoam ground) must be uploaded manually in GitHub Settings → Social preview.

- [ ] **Step 4: Test and commit**

Run: `uv run pytest -q` (nothing should break — this task touches no code).

```bash
git add README.md server.json docs/marketing/repo-metadata.md
git commit -m "Add registry manifest and repo metadata for going public"
```

---

### Task 5: Launch drafts (Show HN + X)

**Files:**
- Create: `docs/marketing/launch-drafts.md`

- [ ] **Step 1: Write the drafts file**

`docs/marketing/launch-drafts.md` containing, verbatim as the starting point (Wouter edits before posting):

```markdown
# Launch drafts — edit before posting

## Show HN

**Title:** Show HN: Reef – shared long-term memory for your household's AI, invite-only by design

**Body:**

I built reef because my partner and I kept telling our assistants the same
things twice. It's a remote MCP server that gives a group long-term memory:
one private cove per person, plus shared coves for any circle with a "we" —
the household, the school run, you and your accountant.

Design choices HN might find interesting:

- Memory is a wiki, not a blob: human-readable Markdown pages behind an
  index-first retrieval pattern (Karpathy's "LLM wiki" idea). You can open,
  edit, and export everything.
- Privacy is enforced in Postgres row-level security, not application code.
  The app connects as a non-superuser; a person's private cove is invisible
  to queries made on someone else's behalf.
- Sharing personal content into a shared cove is a two-step consent ceremony.
- There is no sign-up. You get in when someone already on reef invites you
  (each member can invite 5 people per 30 days). That's the only door, and
  it's deliberate — memory this personal should arrive through trust.

Works from Claude (including the phone app), ChatGPT desktop, and Codex as a
remote MCP connector, plus a CLI (`uv tool install reefwith` or
`npm install -g reefwith`) and an agent skill.

Site: https://reefwith.me — Source: https://github.com/diepzee/rif

## X thread

1/ Your AI assistant forgets everything, and your partner's assistant never
knew it in the first place. reef fixes both: shared, living memory for the
people in your life — readable, editable, yours.

2/ Memory lives in coves: one private cove each, shared coves for any circle
with a "we". The household. The school run. You and your accountant.

3/ It's a wiki, not a memory blob. Real Markdown pages your assistant reads
and tends mid-conversation — and you can open, edit, and export every one.

4/ Privacy isn't a promise in app code. It's Postgres row-level security:
the database itself cannot show your private cove to anyone else's session.

5/ There is no sign-up. Someone already on reef invites you — that's the
only door, and it's deliberate. reefwith.me
```

- [ ] **Step 2: Commit**

```bash
git add docs/marketing/launch-drafts.md
git commit -m "Draft the Show HN and X launch posts"
```

---

### Task 6: Operator checklist (Wouter-only actions, in order)

**Files:**
- Create: `docs/marketing/operator-checklist.md`

- [ ] **Step 1: Write the checklist**

`docs/marketing/operator-checklist.md`:

```markdown
# Going-public checklist (operator actions, in this order)

Sequencing matters: main auto-deploys production, so the site must not
advertise install commands before the packages exist.

1. [ ] Review + merge this branch's PR — EXCEPT do not merge before steps 2–3
       if the PR already contains the new site install copy.
2. [ ] Publish the Python CLI: `uv build clients/python && uv publish`
       (needs a PyPI token for a new `reefwith` project).
3. [ ] Publish the TS CLI: `cd clients/ts && npm publish` (needs `npm login`;
       first publish of `reefwith`).
4. [ ] Verify both installs from clean environments:
       `uv tool install reefwith && reef --help`, `npx reefwith --help`.
5. [ ] Merge the branch; confirm reefwith.me shows the new copy.
6. [ ] Flip the repo public: GitHub → Settings → General → Danger zone →
       Change visibility. (gitleaks scanned all history 2026-08-13: clean.)
7. [ ] Upload the social preview image (Settings → Social preview, 1280×640).
8. [ ] Official MCP registry: install `mcp-publisher`, verify the
       `me.reefwith` namespace via DNS or HTTP, `mcp-publisher publish`
       with the repo's server.json.
9. [ ] mcp.so — submit via their "Submit" flow with https://reefwith.me/mcp.
10. [ ] glama.ai/mcp — claim/submit the server listing.
11. [ ] awesome-mcp-servers — PR adding reef under a memory/knowledge section.
12. [ ] Post Show HN and the X thread (docs/marketing/launch-drafts.md) —
        ideally a weekday morning US time; be around to answer comments.
```

- [ ] **Step 2: Commit and push the branch**

```bash
git add docs/marketing/operator-checklist.md
git commit -m "Add the operator checklist for going public"
git push -u origin marketing-quick-wins
```

---

## Verification (end-to-end)

- `uv run pytest -q` — full suite green (CLI move, static-site copy tests).
- `uv run reef --help` and `node clients/ts/dist/index.js --help` both print usage.
- `uv build clients/python` and `npm pack` (in `clients/ts`) both produce installable artifacts.
- Playwright screenshots of the edited landing-page sections, desktop + mobile, light + dark.
- Spec success criteria walk-through: stranger-legible site, working install commands (post-publish), registry manifest present, launch drafts Wouter would post.
