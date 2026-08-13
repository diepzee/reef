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
