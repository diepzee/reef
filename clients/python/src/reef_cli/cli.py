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

from fastmcp import Client
from fastmcp.client.auth import OAuth

DEFAULT_MCP_URL = "https://reefwith.me/mcp"
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "reef"


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
    sub.add_parser("tools", help="list the MCP server's current tool schemas")
    call = sub.add_parser("call", help="call an exact MCP tool with a JSON object")
    call.add_argument("tool_name")
    call.add_argument(
        "arguments",
        nargs="?",
        default="{}",
        help="JSON object, @file, or '-' for stdin (default: {})",
    )

    _tool_parser(sub, "load_index", "load the body-free memory index")
    read_pages = _tool_parser(sub, "read_pages", "read several pages from one space")
    read_pages.add_argument("space")
    read_pages.add_argument("paths", nargs="+")
    search = _tool_parser(sub, "search_pages", "full-text search across your pages")
    search.add_argument("query")
    search.add_argument("--space", help="restrict to one space")
    search.add_argument("--limit", type=int, default=10)
    _tool_parser(sub, "load_all_context", "load all page bodies for maintenance")
    _tool_parser(sub, "get_operating_protocol", "load the protocol and persona")
    read_page = _tool_parser(sub, "read_page", "read one page")
    read_page.add_argument("space")
    read_page.add_argument("path")
    read_page.add_argument("--as-of", help="ISO-8601 moment to read the page as of")
    whats_new = _tool_parser(sub, "whats_new", "list recent changes across spaces")
    whats_new.add_argument("--since", help="ISO-8601 moment to report changes after")
    _tool_parser(sub, "list_spaces", "list spaces, members, ownership, and versions")
    create_space = _tool_parser(sub, "create_space", "create a shared space")
    create_space.add_argument("slug")
    invite = _tool_parser(sub, "invite", "invite a person into a shared space")
    invite.add_argument("space")
    invite.add_argument("email")
    invite.add_argument("--display-name")
    invite.add_argument(
        "--role",
        choices=["member", "viewer"],
        default="member",
        help="viewer reads everything and writes nothing",
    )
    invite_reef = _tool_parser(
        sub, "invite_to_reef", "invite someone to Reef without sharing a space"
    )
    invite_reef.add_argument("email")
    invite_reef.add_argument("--display-name")
    remove = _tool_parser(sub, "remove_member", "remove a shared-space member")
    remove.add_argument("space")
    remove.add_argument("email")
    rename = _tool_parser(sub, "rename_cove", "change what you call a shared cove")
    rename.add_argument("space")
    rename.add_argument("new_name")
    leave = _tool_parser(sub, "leave_space", "leave a shared space, handing it on")
    leave.add_argument("space")
    delete = _tool_parser(
        sub, "delete_space", "destroy a shared space you are alone in"
    )
    delete.add_argument("space")
    remember = _tool_parser(sub, "remember", "append a fact to a space inbox")
    remember.add_argument("fact", help="fact text; '-' reads stdin")
    remember.add_argument("--space", default="personal")

    write = _tool_parser(sub, "write_page", "create or replace one Markdown page")
    write.add_argument("space")
    write.add_argument("path")
    _add_text_source(write, "body")
    write.add_argument("--message", required=True)
    write.add_argument("--title")
    write.add_argument("--tag", dest="tags", action="append")
    write.add_argument("--expected-version", type=int)

    delete_page = _tool_parser(
        sub, "delete_page", "permanently delete a page and its history"
    )
    delete_page.add_argument("space")
    delete_page.add_argument("path")

    writes = _tool_parser(sub, "write_pages", "atomically write up to 20 pages")
    writes.add_argument("space")
    writes.add_argument("pages", help="JSON array, @file, or '-' for stdin")
    writes.add_argument("--message", default="")

    edit = _tool_parser(sub, "edit_page_section", "replace one exact page span")
    edit.add_argument("space")
    edit.add_argument("path")
    _add_text_source(edit, "old-text")
    _add_text_source(edit, "new-text")
    edit.add_argument("--message", required=True)
    edit.add_argument("--expected-version", type=int)

    meta = _tool_parser(sub, "update_meta_page", "replace the personal persona page")
    _add_text_source(meta, "body")
    meta.add_argument("--message", required=True)
    meta.add_argument(
        "--confirm",
        action="store_true",
        help="confirm that the user agreed to this persona change",
    )

    prepare = _tool_parser(
        sub, "prepare_to_share", "stage personal content for sharing"
    )
    prepare.add_argument("path")
    prepare.add_argument("dest_space")
    prepare.add_argument("--section")
    prepare.add_argument("--section-file")
    prepare.add_argument("--dest-path")
    confirm = _tool_parser(sub, "confirm_share", "execute a staged share")
    confirm.add_argument("nonce")

    add_file = _tool_parser(
        sub, "add_file", "upload a file with a searchable description"
    )
    add_file.add_argument("space")
    add_file.add_argument("file", type=Path)
    add_file.add_argument("--mime")
    add_file.add_argument("--description", required=True)
    add_file.add_argument("--page-path")
    read_file = _tool_parser(sub, "read_file", "get file metadata and a signed URL")
    read_file.add_argument("space")
    read_file.add_argument("key")
    delete_file = _tool_parser(sub, "delete_file", "permanently delete a stored file")
    delete_file.add_argument("space")
    delete_file.add_argument("key")

    add_image = _tool_parser(
        sub, "add_image", "compatibility alias for uploading an image"
    )
    add_image.add_argument("space")
    add_image.add_argument("file", type=Path)
    add_image.add_argument("--mime")
    add_image.add_argument("--description", required=True)
    add_image.add_argument("--page-path")
    read_image = _tool_parser(sub, "read_image", "compatibility alias for read-file")
    read_image.add_argument("space")
    read_image.add_argument("key")
    delete_image = _tool_parser(
        sub, "delete_image", "compatibility alias for delete-file"
    )
    delete_image.add_argument("space")
    delete_image.add_argument("key")
    return parser


def _file_arguments(args: argparse.Namespace, *, image: bool = False) -> dict[str, Any]:
    try:
        data = args.file.read_bytes()
    except OSError as exc:
        raise CLIError(f"could not read file {args.file}: {exc}") from exc
    mime = args.mime or mimetypes.guess_type(args.file.name)[0]
    if mime is None:
        mime = "application/octet-stream"
    result = {
        "space": args.space,
        "data_base64": base64.b64encode(data).decode("ascii"),
        "mime": mime,
        "description": args.description,
        "page_path": args.page_path,
    }
    if not image:
        result["filename"] = args.file.name
    return result


def tool_call(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Translate one parsed named command into its exact MCP call."""
    tool = args.tool
    if tool in {
        "load_index",
        "load_all_context",
        "get_operating_protocol",
        "list_spaces",
    }:
        return tool, {}
    if tool == "read_pages":
        return tool, {"space": args.space, "paths": args.paths}
    if tool == "search_pages":
        return tool, {"query": args.query, "space": args.space, "limit": args.limit}
    if tool == "read_page":
        payload = {"space": args.space, "path": args.path}
        if args.as_of is not None:
            payload["as_of"] = args.as_of
        return tool, payload
    if tool == "whats_new":
        return tool, {"since": args.since} if args.since is not None else {}
    if tool == "create_space":
        return tool, {"slug": args.slug}
    if tool in {"invite", "invite_to_reef"}:
        payload = {"email": args.email, "display_name": args.display_name}
        if tool == "invite":
            payload["space"] = args.space
            payload["role"] = args.role
        return tool, payload
    if tool == "remove_member":
        return tool, {"space": args.space, "email": args.email}
    if tool == "rename_cove":
        return tool, {"space": args.space, "new_name": args.new_name}
    if tool in {"leave_space", "delete_space"}:
        return tool, {"space": args.space}
    if tool == "remember":
        fact = sys.stdin.read() if args.fact == "-" else args.fact
        return tool, {"fact": fact, "space": args.space}
    if tool == "write_page":
        return tool, {
            "space": args.space,
            "path": args.path,
            "body": _text_source(args.body, args.body_file, "body"),
            "message": args.message,
            "title": args.title,
            "tags": args.tags,
            "expected_version": args.expected_version,
        }
    if tool == "write_pages":
        pages = _json_source(args.pages)
        if not isinstance(pages, list):
            raise CLIError("write-pages input must be a JSON array")
        return tool, {"space": args.space, "pages": pages, "message": args.message}
    if tool == "edit_page_section":
        return tool, {
            "space": args.space,
            "path": args.path,
            "old_text": _text_source(args.old_text, args.old_text_file, "old-text"),
            "new_text": _text_source(args.new_text, args.new_text_file, "new-text"),
            "message": args.message,
            "expected_version": args.expected_version,
        }
    if tool == "update_meta_page":
        return tool, {
            "space": "personal",
            "path": "meta/persona.md",
            "body": _text_source(args.body, args.body_file, "body"),
            "message": args.message,
            "confirm": args.confirm,
        }
    if tool == "prepare_to_share":
        if args.section is not None and args.section_file is not None:
            raise CLIError("use only one of --section and --section-file")
        section = None
        if args.section is not None or args.section_file is not None:
            section = _text_source(args.section, args.section_file, "section")
        return tool, {
            "path": args.path,
            "dest_space": args.dest_space,
            "section": section,
            "dest_path": args.dest_path,
        }
    if tool == "confirm_share":
        return tool, {"nonce": args.nonce}
    if tool == "add_file":
        return tool, _file_arguments(args)
    if tool == "add_image":
        return tool, _file_arguments(args, image=True)
    if tool in {"read_file", "delete_file", "read_image", "delete_image"}:
        return tool, {"space": args.space, "key": args.key}
    raise CLIError(f"unsupported command: {tool}")


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


async def run(args: argparse.Namespace, *, client_class: type[Client] = Client) -> int:
    """Execute parsed arguments and return the intended process status."""
    store = _token_store()
    if args.command == "logout":
        oauth = OAuth(mcp_url=args.url, client_name="Reef CLI", token_storage=store)
        await oauth.token_storage_adapter.clear()
        _print_json({"logged_out": True, "url": args.url}, compact=args.compact)
        return 0

    token = os.environ.get("REEF_ACCESS_TOKEN")
    auth: Any
    if args.command == "login" or not token:
        auth = OAuth(mcp_url=args.url, client_name="Reef CLI", token_storage=store)
    else:
        auth = token

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
