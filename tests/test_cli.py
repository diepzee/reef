import base64
import json
import os
from typing import Any, ClassVar

from reef_cli.cli import JsonTokenStore, build_parser, run

from reef.server import mcp


class FakeClient:
    calls: ClassVar[list[tuple[Any, ...]]] = []
    result: ClassVar[Any] = {"ok": True}

    def __init__(self, url, *, auth):
        self.url = url
        self.auth = auth
        type(self).calls.append(("connect", url, auth))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def call_tool(self, name, arguments):
        type(self).calls.append((name, arguments))
        return type(self).result

    async def list_tools(self):
        return [{"name": "load_index"}]

    async def ping(self):
        type(self).calls.append(("ping",))


def _parser_commands(parser):
    action = next(
        action
        for action in parser._actions
        if action.__class__.__name__ == "_SubParsersAction"
    )
    return set(action.choices)


async def test_named_commands_match_every_mcp_tool():
    commands = _parser_commands(build_parser())
    server_tools = {tool.name.replace("_", "-") for tool in await mcp.list_tools()}
    assert commands == server_tools | {"login", "logout", "tools", "call"}


async def test_named_command_calls_exact_mcp_tool(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path))
    FakeClient.calls = []
    FakeClient.result = {"coves": []}
    args = build_parser().parse_args(
        ["--url", "https://example.test/mcp", "load-index"]
    )

    status = await run(args, client_class=FakeClient)

    assert status == 0
    assert FakeClient.calls[0] == (
        "connect",
        "https://example.test/mcp",
        "secret-token",
    )
    assert FakeClient.calls[1] == ("load_index", {})
    assert json.loads(capsys.readouterr().out) == {"coves": []}


async def test_search_pages_maps_query_and_options(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path / "config"))
    FakeClient.calls = []
    FakeClient.result = []
    args = build_parser().parse_args(
        ["search-pages", "vaillant boiler", "--cove", "household", "--limit", "5"]
    )

    status = await run(args, client_class=FakeClient)

    assert status == 0
    assert FakeClient.calls[-1] == (
        "search_pages",
        {"query": "vaillant boiler", "cove": "household", "limit": 5},
    )
    assert json.loads(capsys.readouterr().out) == []


async def test_read_page_maps_as_of(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path / "config"))
    FakeClient.calls = []
    FakeClient.result = {"body": "then"}
    args = build_parser().parse_args(
        ["read-page", "personal", "house.md", "--as-of", "2026-03-01T12:00:00"]
    )

    status = await run(args, client_class=FakeClient)

    assert status == 0
    assert FakeClient.calls[-1] == (
        "read_page",
        {"cove": "personal", "path": "house.md", "as_of": "2026-03-01T12:00:00"},
    )
    assert json.loads(capsys.readouterr().out) == {"body": "then"}


async def test_write_page_reads_body_file_and_maps_options(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path / "config"))
    body = tmp_path / "page.md"
    body.write_text("# Plans\n\nTake the train.\n", encoding="utf-8")
    FakeClient.calls = []
    FakeClient.result = {"cove": "personal", "path": "plans.md", "version": 4}
    args = build_parser().parse_args(
        [
            "write-page",
            "personal",
            "plans.md",
            "--body-file",
            str(body),
            "--message",
            "update travel plan",
            "--title",
            "Plans",
            "--tag",
            "core",
            "--expected-version",
            "3",
        ]
    )

    status = await run(args, client_class=FakeClient)

    assert status == 0
    assert FakeClient.calls[-1] == (
        "write_page",
        {
            "cove": "personal",
            "path": "plans.md",
            "body": "# Plans\n\nTake the train.\n",
            "message": "update travel plan",
            "title": "Plans",
            "tags": ["core"],
            "expected_version": 3,
        },
    )
    assert json.loads(capsys.readouterr().out)["version"] == 4


async def test_add_file_encodes_bytes_and_infers_mime(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path / "config"))
    upload = tmp_path / "note.txt"
    upload.write_bytes(b"hello reef")
    FakeClient.calls = []
    FakeClient.result = {"key": "attachments/abc"}
    args = build_parser().parse_args(
        [
            "add-file",
            "personal",
            str(upload),
            "--description",
            "A greeting",
            "--page-path",
            "notes.md",
        ]
    )

    status = await run(args, client_class=FakeClient)

    assert status == 0
    name, payload = FakeClient.calls[-1]
    assert name == "add_file"
    assert payload == {
        "cove": "personal",
        "filename": "note.txt",
        "data_base64": base64.b64encode(b"hello reef").decode("ascii"),
        "mime": "text/plain",
        "description": "A greeting",
        "page_path": "notes.md",
    }
    assert json.loads(capsys.readouterr().out) == {"key": "attachments/abc"}


async def test_call_is_exact_json_passthrough(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path))
    arguments = tmp_path / "args.json"
    arguments.write_text('{"cove":"personal","paths":["a.md"]}')
    FakeClient.calls = []
    FakeClient.result = [{"path": "a.md", "body": "alpha"}]
    args = build_parser().parse_args(["call", "read_pages", f"@{arguments}"])

    status = await run(args, client_class=FakeClient)

    assert status == 0
    assert FakeClient.calls[-1] == (
        "read_pages",
        {"cove": "personal", "paths": ["a.md"]},
    )
    assert json.loads(capsys.readouterr().out)[0]["body"] == "alpha"


async def test_tool_error_payload_sets_failure_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path))
    FakeClient.calls = []
    FakeClient.result = {"error": "not_found", "path": "missing.md"}
    args = build_parser().parse_args(["read-page", "personal", "missing.md"])

    status = await run(args, client_class=FakeClient)

    assert status == 1
    assert json.loads(capsys.readouterr().out)["error"] == "not_found"


async def test_json_token_store_persists_and_expires(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("reef_cli.cli.time.time", lambda: clock[0])
    path = tmp_path / "reef" / "oauth.json"
    store = JsonTokenStore(path)

    await store.put("token", {"access": "secret"}, collection="oauth", ttl=10)

    assert await JsonTokenStore(path).get("token", collection="oauth") == {
        "access": "secret"
    }
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
    clock[0] = 1011.0
    assert await store.get("token", collection="oauth") is None
