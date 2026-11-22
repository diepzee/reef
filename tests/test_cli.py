import base64
import json
import os
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
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
    assert commands == server_tools | {
        "login",
        "logout",
        "tools",
        "call",
        "reset-test-account",
    }


async def test_named_command_calls_exact_mcp_tool(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path))
    FakeClient.calls = []
    FakeClient.result = {"spaces": []}
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
    assert FakeClient.calls[1] == ("load_index", {"reason": ""})
    assert json.loads(capsys.readouterr().out) == {"spaces": []}


async def _call(monkeypatch, tmp_path, argv, result=None):
    """Run one named command against the fake client.

    :param monkeypatch: pytest's monkeypatch
    :param tmp_path: a scratch directory for the CLI's config
    :param argv: the command line after ``reef``
    :param result: what the fake server answers
    :returns: the exit status
    """
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path / "config"))
    FakeClient.calls = []
    FakeClient.result = {"ok": True} if result is None else result
    return await run(build_parser().parse_args(argv), client_class=FakeClient)


async def test_recall_maps_question_and_options(monkeypatch, tmp_path, capsys):
    """Recall sends the question, its options and the reason."""
    status = await _call(
        monkeypatch,
        tmp_path,
        ["recall", "vaillant boiler", "--space", "household", "--limit", "3"]
        + ["--reason", "asked about the boiler"],
        [],
    )

    assert status == 0
    assert FakeClient.calls[-1] == (
        "recall",
        {
            "reason": "asked about the boiler",
            "question": "vaillant boiler",
            "space": "household",
            "limit": 3,
        },
    )
    assert json.loads(capsys.readouterr().out) == []


async def test_reason_is_optional_and_empty_by_default(monkeypatch, tmp_path):
    """Leaving out --reason sends an empty reason."""
    await _call(monkeypatch, tmp_path, ["brief", "the move"])

    assert FakeClient.calls[-1] == (
        "brief",
        {"reason": "", "topic": "the move", "entries": 10},
    )


async def test_search_and_fetch_send_no_reason(monkeypatch, tmp_path):
    """The connector tools take no reason, so none is sent."""
    await _call(monkeypatch, tmp_path, ["search", "boiler"])
    assert FakeClient.calls[-1] == ("search", {"query": "boiler"})
    await _call(monkeypatch, tmp_path, ["fetch", "engram:1"])
    assert FakeClient.calls[-1] == ("fetch", {"id": "engram:1"})


async def test_open_maps_refs_and_as_of(monkeypatch, tmp_path, capsys):
    """Open sends every ref and the moment."""
    status = await _call(
        monkeypatch,
        tmp_path,
        ["open", "personal/house.md", "personal/file/k", "--as-of", "2026-03-01"],
        [{"body": "then"}],
    )

    assert status == 0
    assert FakeClient.calls[-1] == (
        "open",
        {
            "reason": "",
            "refs": ["personal/house.md", "personal/file/k"],
            "as_of": "2026-03-01",
        },
    )


async def test_catch_up_and_remember_and_log(monkeypatch, tmp_path):
    """Catch up, remember and log map their arguments."""
    await _call(monkeypatch, tmp_path, ["catch-up", "--since", "2026-08-01"])
    assert FakeClient.calls[-1] == (
        "catch_up",
        {"reason": "", "since": "2026-08-01"},
    )
    await _call(monkeypatch, tmp_path, ["remember", "The boiler is a Vaillant"])
    assert FakeClient.calls[-1] == (
        "remember",
        {"reason": "", "fact": "The boiler is a Vaillant", "space": "personal"},
    )
    await _call(
        monkeypatch,
        tmp_path,
        ["log", "personal", "climbing-log.md", "--heading", "2026-11-19"]
        + ["--entry", "Sent the blue route."],
    )
    assert FakeClient.calls[-1] == (
        "log",
        {
            "reason": "",
            "space": "personal",
            "engram": "climbing-log.md",
            "heading": "2026-11-19",
            "entry": "Sent the blue route.",
        },
    )


async def test_write_reads_the_json_array(monkeypatch, tmp_path, capsys):
    """Write reads the engrams from a JSON file."""
    engrams = tmp_path / "engrams.json"
    engrams.write_text('[{"path": "plans.md", "body": "# Plans"}]', encoding="utf-8")

    status = await _call(
        monkeypatch,
        tmp_path,
        ["write", "personal", f"@{engrams}", "--message", "add the plan"],
        [{"path": "plans.md", "version": 1}],
    )

    assert status == 0
    assert FakeClient.calls[-1] == (
        "write",
        {
            "reason": "",
            "space": "personal",
            "engrams": [{"path": "plans.md", "body": "# Plans"}],
            "message": "add the plan",
        },
    )


async def test_correct_and_undo(monkeypatch, tmp_path):
    """Correct and undo map their arguments."""
    await _call(
        monkeypatch,
        tmp_path,
        ["correct", "personal", "plans.md", "--old-text", "train"]
        + ["--new-text", "bus", "--message", "fix", "--expected-version", "3"],
    )
    assert FakeClient.calls[-1] == (
        "correct",
        {
            "reason": "",
            "space": "personal",
            "engram": "plans.md",
            "old_text": "train",
            "new_text": "bus",
            "message": "fix",
            "expected_version": 3,
        },
    )
    await _call(monkeypatch, tmp_path, ["undo", "personal", "plans.md"])
    assert FakeClient.calls[-1] == (
        "undo",
        {"reason": "", "space": "personal", "engram": "plans.md"},
    )


async def test_store_file_encodes_bytes_and_infers_mime(monkeypatch, tmp_path, capsys):
    """Store file encodes the bytes and guesses the content type."""
    upload = tmp_path / "note.txt"
    upload.write_bytes(b"hello reef")

    status = await _call(
        monkeypatch,
        tmp_path,
        ["store-file", "personal", str(upload), "--description", "A greeting"]
        + ["--engram", "notes.md"],
        {"key": "attachments/abc"},
    )

    assert status == 0
    name, payload = FakeClient.calls[-1]
    assert name == "store_file"
    assert payload == {
        "reason": "",
        "space": "personal",
        "filename": "note.txt",
        "data_base64": base64.b64encode(b"hello reef").decode("ascii"),
        "mime": "text/plain",
        "description": "A greeting",
        "engram": "notes.md",
    }
    assert json.loads(capsys.readouterr().out) == {"key": "attachments/abc"}


async def test_staged_tools_take_a_confirm_token_for_the_second_call(
    monkeypatch, tmp_path
):
    """Share, forget and instruct send the token only when given."""
    await _call(monkeypatch, tmp_path, ["forget", "personal", "--engram", "a.md"])
    assert FakeClient.calls[-1] == (
        "forget",
        {"reason": "", "space": "personal", "engram": "a.md"},
    )
    await _call(
        monkeypatch,
        tmp_path,
        ["forget", "personal", "--file", "k", "--confirm", "tok"],
    )
    assert FakeClient.calls[-1] == (
        "forget",
        {"reason": "", "space": "personal", "file": "k", "confirm": "tok"},
    )
    await _call(
        monkeypatch,
        tmp_path,
        ["share", "a.md", "family", "--section", "x", "--to-path", "b.md"]
        + ["--confirm", "tok"],
    )
    assert FakeClient.calls[-1] == (
        "share",
        {
            "reason": "",
            "engram": "a.md",
            "to_space": "family",
            "section": "x",
            "to_path": "b.md",
            "confirm": "tok",
        },
    )
    await _call(monkeypatch, tmp_path, ["instruct", "--body", "Be brief."])
    assert FakeClient.calls[-1] == ("instruct", {"reason": "", "body": "Be brief."})
    await _call(
        monkeypatch, tmp_path, ["instruct", "--body", "Be brief.", "--confirm", "t"]
    )
    assert FakeClient.calls[-1][1]["confirm"] == "t"


async def test_call_is_exact_json_passthrough(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path))
    arguments = tmp_path / "args.json"
    arguments.write_text('{"refs":["personal/a.md"]}')
    FakeClient.calls = []
    FakeClient.result = [{"path": "a.md", "body": "alpha"}]
    args = build_parser().parse_args(["call", "open", f"@{arguments}"])

    status = await run(args, client_class=FakeClient)

    assert status == 0
    assert FakeClient.calls[-1] == (
        "open",
        {"refs": ["personal/a.md"]},
    )
    assert json.loads(capsys.readouterr().out)[0]["body"] == "alpha"


async def test_tool_error_payload_sets_failure_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path))
    FakeClient.calls = []
    FakeClient.result = {"error": "not_found", "path": "missing.md"}
    args = build_parser().parse_args(["open", "personal/missing.md"])

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


RESET_BODY = {
    "fixture_version": "v1",
    "engrams": 12,
    "files": 3,
    "services": ["search"],
    "reef_version": "1.2.3",
}


async def _reset(
    monkeypatch: Any,
    tmp_path: Any,
    handler: Callable[[httpx.Request], httpx.Response],
    argv: list[str],
) -> int:
    """Run ``reset-test-account`` against a mocked server.

    :param monkeypatch: pytest's monkeypatch
    :param tmp_path: a scratch directory for the CLI's config
    :param handler: answers every request the command makes
    :param argv: the command line after ``reef``
    :returns: the exit status
    """
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path))
    args = build_parser().parse_args(argv)
    return await run(args, transport=httpx.MockTransport(handler), poll_interval=0.01)


async def test_reset_polls_until_ready(monkeypatch, tmp_path, capsys):
    """The command resets, polls until ready and reports how long it took."""
    requests: list[httpx.Request] = []
    ready = iter([False, False, True])

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer the reset and then three readiness checks."""
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json=RESET_BODY)
        return httpx.Response(
            200,
            json={
                "ready": next(ready),
                "vectors_missing": 0,
                "files_unread": 0,
                "jobs_waiting": 0,
            },
        )

    status = await _reset(
        monkeypatch,
        tmp_path,
        handler,
        ["--url", "https://example.test/mcp", "reset-test-account"],
    )

    out = json.loads(capsys.readouterr().out)
    assert status == 0
    assert out["reset"] is True
    assert out["engrams"] == 12
    assert out["reef_version"] == "1.2.3"
    assert isinstance(out["seconds"], (int, float))
    assert [r.method for r in requests] == ["POST", "GET", "GET", "GET"]
    assert all(r.headers["authorization"] == "Bearer secret-token" for r in requests)


async def test_reset_refused(monkeypatch, tmp_path, capsys):
    """A refusal prints the server's code and fails."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Refuse the reset."""
        return httpx.Response(403, json={"error": "not_a_test_account"})

    status = await _reset(
        monkeypatch, tmp_path, handler, ["--compact", "reset-test-account"]
    )

    assert status == 1
    assert json.loads(capsys.readouterr().out) == {"error": "not_a_test_account"}


async def test_reset_server_error(monkeypatch, tmp_path, capsys):
    """A 5xx answer prints ``server_error`` and fails."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Fail the reset."""
        return httpx.Response(502, text="bad gateway")

    status = await _reset(monkeypatch, tmp_path, handler, ["reset-test-account"])

    assert status == 1
    assert json.loads(capsys.readouterr().out) == {"error": "server_error"}


async def test_reset_times_out(monkeypatch, tmp_path, capsys):
    """When the account never becomes ready the command fails with not_ready."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Reset fine, never get ready."""
        if request.method == "POST":
            return httpx.Response(200, json=RESET_BODY)
        return httpx.Response(200, json={"ready": False})

    status = await _reset(
        monkeypatch, tmp_path, handler, ["reset-test-account", "--timeout", "1"]
    )

    assert status == 1
    assert json.loads(capsys.readouterr().out) == {"error": "not_ready"}


async def test_base_url_strips_mcp(monkeypatch, tmp_path, capsys):
    """The routes live beside ``/mcp``, not under it."""
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the URL and answer ready."""
        urls.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(200, json=RESET_BODY)
        return httpx.Response(200, json={"ready": True})

    await _reset(
        monkeypatch,
        tmp_path,
        handler,
        ["--url", "https://example.test/mcp", "reset-test-account"],
    )

    assert urls[0] == "https://example.test/api/test-account/reset"
    assert urls[1] == "https://example.test/api/test-account/ready"
