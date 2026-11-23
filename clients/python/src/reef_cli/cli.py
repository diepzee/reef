"""Command-line client for Reef's remote MCP server.

The named commands are deliberately thin adapters over MCP tool calls.  The
``call`` command is the escape hatch which preserves the exact MCP surface for
new tools and unusual argument shapes without waiting for a CLI release.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, SupportsFloat
from urllib.parse import urlsplit

import httpx
from fastmcp import Client
from fastmcp.client.auth import OAuth

DEFAULT_MCP_URL = "https://reefwith.me/mcp"
DEFAULT_RESET_TIMEOUT = 300.0
READY_POLL_SECONDS = 2.0
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "reef"
#: ``reef token`` refreshes a token that runs out within this many seconds.
DEFAULT_TOKEN_MARGIN = 60.0


class CLIError(Exception):
    """A user-facing command-line error."""


class JsonTokenStore:
    """Small persistent ``AsyncKeyValue`` store for FastMCP OAuth state.

    FastMCP accepts any implementation of its async key-value protocol.  A
    single JSON file keeps the CLI dependency-light and portable.  The parent
    directory and file are user-only on POSIX systems, and writes replace the
    file atomically so an interrupted login cannot leave half-written JSON.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise CLIError(f"could not read OAuth state at {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise CLIError(f"OAuth state at {self.path} is not a JSON object")
        return raw

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "posix":
                self.path.parent.chmod(0o700)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(data, separators=(",", ":")), encoding="utf-8"
            )
            if os.name == "posix":
                temporary.chmod(0o600)
            temporary.replace(self.path)
        except OSError as exc:
            raise CLIError(
                f"could not write OAuth state at {self.path}: {exc}"
            ) from exc

    @staticmethod
    def _entry_key(key: str, collection: str | None) -> str:
        return f"{collection or 'default'}\0{key}"

    async def get(
        self, key: str, *, collection: str | None = None
    ) -> dict[str, Any] | None:
        data = self._read()
        entry_key = self._entry_key(key, collection)
        entry = data.get(entry_key)
        if entry is None:
            return None
        expires_at = entry.get("expires_at")
        if expires_at is not None and float(expires_at) <= time.time():
            del data[entry_key]
            self._write(data)
            return None
        value = entry.get("value")
        return value if isinstance(value, dict) else None

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        value = await self.get(key, collection=collection)
        if value is None:
            return None, None
        entry = self._read()[self._entry_key(key, collection)]
        expires_at = entry.get("expires_at")
        remaining = None if expires_at is None else max(0.0, expires_at - time.time())
        return value, remaining

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        data = self._read()
        data[self._entry_key(key, collection)] = {
            "value": dict(value),
            "expires_at": None if ttl is None else time.time() + float(ttl),
        }
        self._write(data)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        data = self._read()
        removed = data.pop(self._entry_key(key, collection), None) is not None
        if removed:
            self._write(data)
        return removed

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        return [await self.get(key, collection=collection) for key in keys]

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [await self.ttl(key, collection=collection) for key in keys]

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        if len(keys) != len(values):
            raise ValueError("keys and values must have the same length")
        for key, value in zip(keys, values, strict=True):
            await self.put(key, value, collection=collection, ttl=ttl)

    async def delete_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> int:
        removed = 0
        for key in keys:
            removed += await self.delete(key, collection=collection)
        return removed


def _config_dir() -> Path:
    override = os.environ.get("REEF_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "reef"
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "reef"
    return DEFAULT_CONFIG_DIR


def _token_store() -> JsonTokenStore:
    return JsonTokenStore(_config_dir() / "oauth.json")


def _text_source(value: str | None, file: str | None, label: str) -> str:
    if value is not None:
        return sys.stdin.read() if value == "-" else value
    if file is None:
        raise CLIError(f"one of --{label} or --{label}-file is required")
    if file == "-":
        return sys.stdin.read()
    try:
        return Path(file).read_text(encoding="utf-8")
    except OSError as exc:
        raise CLIError(f"could not read {label} file {file}: {exc}") from exc


def _json_source(source: str) -> Any:
    if source == "-":
        text = sys.stdin.read()
        origin = "stdin"
    elif source.startswith("@"):
        origin = source[1:]
        try:
            text = Path(origin).read_text(encoding="utf-8")
        except OSError as exc:
            raise CLIError(f"could not read JSON file {origin}: {exc}") from exc
    else:
        text = source
        origin = "argument"
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CLIError(f"invalid JSON from {origin}: {exc}") from exc


def _add_text_source(parser: argparse.ArgumentParser, name: str) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        f"--{name}", help=f"{name.replace('-', ' ')} text; '-' reads stdin"
    )
    group.add_argument(
        f"--{name}-file",
        metavar="PATH",
        help=f"read {name.replace('-', ' ')} from PATH",
    )


def _seconds(value: str) -> float:
    """Read a number of seconds that is zero or more.

    :param value: the text given on the command line
    :returns: the number
    :raises argparse.ArgumentTypeError: when it is not a number, is
        negative or is NaN
    """
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from exc
    if not seconds >= 0:  # also refuses NaN, which compares false
        raise argparse.ArgumentTypeError(f"must be 0 or more: {value}")
    return seconds


def _tool_parser(
    subparsers: argparse._SubParsersAction, name: str, help: str
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name.replace("_", "-"), help=help)
    parser.set_defaults(tool=name)
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Build the complete CLI parser."""
    parser = argparse.ArgumentParser(
        prog="reef", description="Read and write shared Reef memory over MCP."
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("REEF_MCP_URL", DEFAULT_MCP_URL),
        help="MCP endpoint (default: %(default)s; env: REEF_MCP_URL)",
    )
    parser.add_argument(
        "--compact", action="store_true", help="emit compact rather than indented JSON"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="sign in through MCP OAuth and cache the tokens")
    sub.add_parser("logout", help="remove cached OAuth tokens for this endpoint")
    token = sub.add_parser(
        "token", help="print a valid access token for this endpoint (for scripts)"
    )
    token.add_argument(
        "--refresh-within",
        type=_seconds,
        default=DEFAULT_TOKEN_MARGIN,
        metavar="SECONDS",
        help="refresh the token first when it runs out within SECONDS "
        "(default: %(default)s)",
    )
    sub.add_parser("tools", help="list the MCP server's current tool schemas")
    reset = sub.add_parser(
        "reset-test-account",
        help="wipe a test account and reload its fixed memory (test accounts only)",
    )
    reset.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_RESET_TIMEOUT,
        metavar="SECONDS",
        help="how long to wait for search and file reading (default: %(default)s)",
    )
    call = sub.add_parser("call", help="call an exact MCP tool with a JSON object")
    call.add_argument("tool_name")
    call.add_argument(
        "arguments",
        nargs="?",
        default="{}",
        help="JSON object, @file, or '-' for stdin (default: {})",
    )

    _build_tool_parsers(sub)
    return parser


def _reasoned(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Give a tool's parser the optional ``--reason`` every tool but two takes.

    :param parser: the tool's parser
    :returns: the same parser
    """
    parser.add_argument(
        "--reason",
        default="",
        help="why you make this call, in a few words; Reef logs it privately "
        "for the person, to improve Reef",
    )
    return parser


def _confirmable(parser: argparse.ArgumentParser) -> None:
    """Give a staged tool's parser the ``--confirm`` of its second call.

    :param parser: the parser of ``share``, ``forget`` or ``instruct``
    """
    parser.add_argument(
        "--confirm",
        metavar="TOKEN",
        help="the token the first call returned, once the person said yes; "
        "leave out for the first call",
    )


def _build_tool_parsers(sub: argparse._SubParsersAction) -> None:
    """Add one subcommand for each of the sixteen tools.

    :param sub: the subparsers of the main parser
    """

    def tool(name: str, help: str, *, reason: bool = True) -> argparse.ArgumentParser:
        parser = _tool_parser(sub, name, help)
        return _reasoned(parser) if reason else parser

    tool("load_index", "start here: protocol, persona and the index of every space")
    recall = tool("recall", "answer a question from memory")
    recall.add_argument("question")
    recall.add_argument("--space", help="search one space only")
    recall.add_argument("--limit", type=int, default=5)
    brief = tool("brief", "get ready to advise or prepare on a topic")
    brief.add_argument("topic")
    brief.add_argument("--space", help="look in one space only")
    brief.add_argument("--entries", type=int, default=10)
    open_ = tool("open", "read whole engrams and stored files by ref")
    open_.add_argument("refs", nargs="+", help="<space>/<path> or <space>/file/<key>")
    open_.add_argument("--as-of", help="ISO-8601 moment to read engrams as of")
    catch_up = tool("catch_up", "list what changed in your spaces")
    catch_up.add_argument("--since", help="ISO-8601 moment; default: last 7 days")
    remember = tool("remember", "keep one quick fact in a space's inbox")
    remember.add_argument("fact", help="fact text; '-' reads stdin")
    remember.add_argument("--space", default="personal")
    log = tool("log", "add one entry at the end of a log")
    log.add_argument("space")
    log.add_argument("engram", help="the log's path")
    log.add_argument("--heading", required=True)
    _add_text_source(log, "entry")
    write = tool("write", "create or replace up to 20 whole engrams, atomically")
    write.add_argument("space")
    write.add_argument("engrams", help="JSON array, @file, or '-' for stdin")
    write.add_argument("--message", required=True)
    store = tool("store_file", "keep a document or an image with a description")
    store.add_argument("space")
    store.add_argument("file", type=Path)
    store.add_argument("--mime")
    store.add_argument("--description", required=True)
    store.add_argument("--engram", help="path of the engram the file belongs to")
    correct = tool("correct", "replace one exact passage of an engram")
    correct.add_argument("space")
    correct.add_argument("engram")
    _add_text_source(correct, "old-text")
    _add_text_source(correct, "new-text")
    correct.add_argument("--message", required=True)
    correct.add_argument("--expected-version", type=int)
    undo = tool("undo", "restore an earlier version of an engram")
    undo.add_argument("space")
    undo.add_argument("engram")
    undo.add_argument("--version", type=int)
    forget = tool("forget", "delete an engram or a file for good, in two calls")
    forget.add_argument("space")
    target = forget.add_mutually_exclusive_group(required=True)
    target.add_argument("--engram", help="path of the engram to forget")
    target.add_argument("--file", help="key of the file to forget")
    _confirmable(forget)
    share = tool("share", "move a personal engram into a shared space, in two calls")
    share.add_argument("engram")
    share.add_argument("to_space")
    share.add_argument("--section", help="exact text to share; '-' reads stdin")
    share.add_argument("--section-file", metavar="PATH")
    share.add_argument(
        "--to-path", help="path of the shared engram; needed with a section"
    )
    _confirmable(share)
    instruct = tool("instruct", "replace the persona, in two calls")
    _add_text_source(instruct, "body")
    _confirmable(instruct)
    # The two-tool connector shape, carried here for parity with the MCP
    # surface. They take no reason: some clients can only ever call these two.
    search = tool("search", "search, in the connector shape", reason=False)
    search.add_argument("query")
    fetch = tool("fetch", "fetch one result from search", reason=False)
    fetch.add_argument("id")


def _file_arguments(args: argparse.Namespace) -> dict[str, Any]:
    """Read a file for ``store_file``: its bytes, name and content type.

    :param args: the parsed ``store-file`` command
    :returns: the arguments of the tool
    :raises CLIError: when the file cannot be read
    """
    try:
        data = args.file.read_bytes()
    except OSError as exc:
        raise CLIError(f"could not read file {args.file}: {exc}") from exc
    mime = args.mime or mimetypes.guess_type(args.file.name)[0]
    return {
        "space": args.space,
        "filename": args.file.name,
        "data_base64": base64.b64encode(data).decode("ascii"),
        "mime": mime or "application/octet-stream",
        "description": args.description,
        "engram": args.engram,
    }


def _arguments(args: argparse.Namespace) -> dict[str, Any]:
    """Turn a parsed tool command into the tool's arguments, without ``reason``.

    :param args: the parsed command
    :returns: the arguments; optional ones that were not given are left out
    :raises CLIError: when an argument cannot be used
    """
    tool = args.tool
    if tool == "recall":
        return {"question": args.question, "space": args.space, "limit": args.limit}
    if tool == "brief":
        return {"topic": args.topic, "space": args.space, "entries": args.entries}
    if tool == "open":
        return {"refs": args.refs, "as_of": args.as_of}
    if tool == "catch_up":
        return {"since": args.since}
    if tool == "remember":
        fact = sys.stdin.read() if args.fact == "-" else args.fact
        return {"fact": fact, "space": args.space}
    if tool == "log":
        return {
            "space": args.space,
            "engram": args.engram,
            "heading": args.heading,
            "entry": _text_source(args.entry, args.entry_file, "entry"),
        }
    if tool == "write":
        engrams = _json_source(args.engrams)
        if not isinstance(engrams, list):
            raise CLIError("write input must be a JSON array")
        return {"space": args.space, "engrams": engrams, "message": args.message}
    if tool == "store_file":
        return _file_arguments(args)
    if tool == "correct":
        return {
            "space": args.space,
            "engram": args.engram,
            "old_text": _text_source(args.old_text, args.old_text_file, "old-text"),
            "new_text": _text_source(args.new_text, args.new_text_file, "new-text"),
            "message": args.message,
            "expected_version": args.expected_version,
        }
    if tool == "undo":
        return {"space": args.space, "engram": args.engram, "version": args.version}
    if tool == "forget":
        return {
            "space": args.space,
            "engram": args.engram,
            "file": args.file,
            "confirm": args.confirm,
        }
    if tool == "share":
        if args.section is not None and args.section_file is not None:
            raise CLIError("use only one of --section and --section-file")
        section = None
        if args.section is not None or args.section_file is not None:
            section = _text_source(args.section, args.section_file, "section")
        return {
            "engram": args.engram,
            "to_space": args.to_space,
            "section": section,
            "to_path": args.to_path,
            "confirm": args.confirm,
        }
    if tool == "instruct":
        return {
            "body": _text_source(args.body, args.body_file, "body"),
            "confirm": args.confirm,
        }
    if tool == "search":
        return {"query": args.query}
    if tool == "fetch":
        return {"id": args.id}
    return {}


def tool_call(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Translate one parsed named command into its exact MCP call.

    :param args: the parsed command
    :returns: the tool's name and its arguments: ``reason`` first, when the
        tool takes one, then every argument that was given
    :raises CLIError: when an argument cannot be used
    """
    tool = args.tool
    arguments = {k: v for k, v in _arguments(args).items() if v is not None}
    if tool not in {"search", "fetch"}:
        arguments = {"reason": args.reason, **arguments}
    return tool, arguments


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _print_json(value: Any, *, compact: bool) -> None:
    separators = (",", ":") if compact else None
    print(
        json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            indent=None if compact else 2,
            separators=separators,
        )
    )


def _error_code(response: httpx.Response) -> str:
    """Read the error code of a refused request.

    :param response: the non-200 response
    :returns: the server's code, or ``server_error`` for a 5xx or an unreadable body
    """
    if response.status_code >= 500:
        return "server_error"
    try:
        body = response.json()
    except ValueError:
        return "server_error"
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) else "server_error"


async def reset_test_account(
    args: argparse.Namespace,
    auth: Any,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    poll_interval: float = READY_POLL_SECONDS,
) -> tuple[dict[str, Any], int]:
    """Reset a test account, then wait until search and file reading work.

    :param args: the parsed command, with ``url`` and ``timeout``
    :param auth: a bearer token, or the OAuth object that signs requests
    :param transport: replaces the network, for tests
    :param poll_interval: seconds between readiness checks
    :returns: what to print, and the exit status
    """
    base = args.url.removesuffix("/").removesuffix("/mcp")
    headers = {"Authorization": f"Bearer {auth}"} if isinstance(auth, str) else {}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            base_url=base,
            headers=headers,
            auth=None if isinstance(auth, str) else auth,
            transport=transport,
            timeout=60.0,
        ) as http:
            response = await http.post("/api/test-account/reset")
            if response.status_code != 200:
                return {"error": _error_code(response)}, 1
            report = response.json()
            while True:
                ready = await http.get("/api/test-account/ready")
                if ready.status_code != 200:
                    return {"error": _error_code(ready)}, 1
                if ready.json().get("ready"):
                    break
                if time.monotonic() - started >= args.timeout:
                    return {"error": "not_ready"}, 1
                await asyncio.sleep(poll_interval)
    except (httpx.HTTPError, ValueError):
        return {"error": "server_error"}, 1
    keys = ("fixture_version", "engrams", "files", "services", "reef_version")
    result = {"reset": True, **{key: report.get(key) for key in keys}}
    result["seconds"] = round(time.monotonic() - started, 1)
    return result, 0


async def access_token(
    url: str,
    store: JsonTokenStore,
    *,
    refresh_within: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str | None, str | None]:
    """Find a valid access token in the cached sign-in, refreshed when needed.

    Never starts a sign-in: without a usable cached sign-in it gives up. A
    refresh goes to the server's ``/token``, as the OAuth client's own
    refresh does, and the new tokens replace the cached ones.

    :param url: the MCP endpoint the sign-in is for
    :param store: the cached OAuth state
    :param refresh_within: refresh first when the token runs out within this
        many seconds
    :param transport: replaces the network, for tests
    :returns: the token and None; or None and the error code,
        ``not_signed_in`` or ``server_error``
    """
    oauth = OAuth(mcp_url=url, client_name="Reef CLI", token_storage=store)
    saved = oauth.token_storage_adapter
    tokens = await saved.get_tokens()
    if tokens is None:
        return None, "not_signed_in"
    expires_at = await saved.get_token_expiry()
    if expires_at is None and tokens.expires_in is None:
        return tokens.access_token, None
    if expires_at is not None and expires_at - time.time() > refresh_within:
        return tokens.access_token, None
    client = await saved.get_client_info()
    if not tokens.refresh_token or client is None or not client.client_id:
        return None, "not_signed_in"
    oauth.context.client_info = client
    form, headers = oauth.context.prepare_token_auth(
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
            "client_id": client.client_id,
        },
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    parts = urlsplit(url)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=30.0) as http:
            response = await http.post(
                f"{parts.scheme}://{parts.netloc}/token", data=form, headers=headers
            )
    except httpx.HTTPError:
        return None, "server_error"
    if response.status_code in (400, 401):
        return None, "not_signed_in"
    if response.status_code != 200:
        return None, "server_error"
    try:
        # The cached token's own model, so the CLI needs no import of ``mcp``.
        fresh = type(tokens).model_validate_json(response.content)
    except ValueError:
        # Never echo the body: it may hold a token.
        return None, "server_error"
    if fresh.refresh_token is None:
        fresh = fresh.model_copy(update={"refresh_token": tokens.refresh_token})
    await saved.set_tokens(fresh)
    return fresh.access_token, None


async def run(
    args: argparse.Namespace,
    *,
    client_class: type[Client] = Client,
    transport: httpx.AsyncBaseTransport | None = None,
    poll_interval: float = READY_POLL_SECONDS,
) -> int:
    """Execute parsed arguments and return the intended process status.

    :param args: the parsed command
    :param client_class: the MCP client, replaced in tests
    :param transport: the HTTP transport of ``reset-test-account`` and
        ``token``, replaced in tests
    :param poll_interval: seconds between ``reset-test-account`` readiness checks
    :returns: the process status
    """
    store = _token_store()
    if args.command == "logout":
        oauth = OAuth(mcp_url=args.url, client_name="Reef CLI", token_storage=store)
        await oauth.token_storage_adapter.clear()
        _print_json({"logged_out": True, "url": args.url}, compact=args.compact)
        return 0

    if args.command == "token":
        # Only the cached sign-in counts: ``REEF_ACCESS_TOKEN`` is not read.
        found, error = await access_token(
            args.url, store, refresh_within=args.refresh_within, transport=transport
        )
        if found is None:
            print(json.dumps({"error": error}), file=sys.stderr)
            return 1
        print(found)
        return 0

    token = os.environ.get("REEF_ACCESS_TOKEN")
    auth: Any
    if args.command == "login" or not token:
        auth = OAuth(mcp_url=args.url, client_name="Reef CLI", token_storage=store)
    else:
        auth = token

    if args.command == "reset-test-account":
        result, status = await reset_test_account(
            args, auth, transport=transport, poll_interval=poll_interval
        )
        _print_json(result, compact=args.compact)
        return status

    async with client_class(args.url, auth=auth) as client:
        if args.command == "login":
            await client.ping()
            result: Any = {"logged_in": True, "url": args.url}
        elif args.command == "tools":
            result = await client.list_tools()
        elif args.command == "call":
            arguments = _json_source(args.arguments)
            if not isinstance(arguments, dict):
                raise CLIError("call arguments must be a JSON object")
            result = await client.call_tool(args.tool_name, arguments)
        else:
            name, arguments = tool_call(args)
            result = await client.call_tool(name, arguments)

    serialised = _jsonable(result)
    _print_json(serialised, compact=args.compact)
    return 1 if isinstance(serialised, dict) and "error" in serialised else 0


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        status = asyncio.run(run(args))
    except CLIError as exc:
        parser.exit(2, f"reef: {exc}\n")
    except KeyboardInterrupt:
        parser.exit(130, "reef: interrupted\n")
    except Exception as exc:  # noqa: BLE001 - CLI boundary turns failures into one line
        parser.exit(1, f"reef: {exc}\n")
    raise SystemExit(status)


if __name__ == "__main__":
    main()
