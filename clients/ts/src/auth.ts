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
  OAuthClientInformationMixed,
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

  clientInformation(): OAuthClientInformation | OAuthClientInformationFull | undefined {
    return this.store.get(this.url).clientInformation;
  }

  saveClientInformation(info: OAuthClientInformationMixed): void {
    this.store.set(this.url, {
      ...this.store.get(this.url),
      clientInformation: info as OAuthClientInformationFull,
    });
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
