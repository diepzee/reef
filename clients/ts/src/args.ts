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
      throw new UsageError(USAGE);
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
