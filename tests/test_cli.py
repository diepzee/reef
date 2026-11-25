import base64
import json
import os
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.auth import OAuth
from jsonschema import validate
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
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
        "token",
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


# Command arguments only; schemas come from the server's tool list below.
TOOL_ARGUMENT_CASES: dict[str, tuple[list[str], list[str]]] = {
    "load_index": ([], []),
    "recall": (["boiler"], ["--space", "household", "--limit", "3"]),
    "brief": (["climbing"], ["--space", "personal", "--entries", "2"]),
    "open": (
        ["personal/notes.md", "household/file/attachments/key#page=2"],
        ["--as-of", "2026-01-01T00:00:00Z"],
    ),
    "catch_up": ([], ["--since", "2026-01-01T00:00:00Z"]),
    "remember": (["The boiler is a Vaillant."], ["--space", "household"]),
    "log": (
        ["personal", "training-log.md", "--heading", "Day 1", "--entry", "Climbed."],
        [],
    ),
    "write": (
        [
            "personal",
            json.dumps(
                [
                    {
                        "path": "notes.md",
                        "body": "Notes.",
                        "title": "Notes",
                        "tags": ["home"],
                        "expected_version": 1,
                        "message": "edit",
                    }
                ]
            ),
            "--message",
            "save",
        ],
        [],
    ),
    "store_file": (
        ["personal", "{file}", "--description", "Boiler manual."],
        ["--mime", "application/pdf", "--engram", "notes.md"],
    ),
    "correct": (
        [
            "personal",
            "notes.md",
            "--old-text",
            "old",
            "--new-text",
            "new",
            "--message",
            "fix",
        ],
        ["--expected-version", "2"],
    ),
    "undo": (["personal", "notes.md"], ["--version", "1"]),
    "forget": (["personal", "--engram", "notes.md"], ["--confirm", "token"]),
    "share": (
        ["notes.md", "household"],
        ["--section", "Boiler notes.", "--to-path", "shared.md", "--confirm", "token"],
    ),
    "instruct": (["--body", "Be concise."], ["--confirm", "token"]),
    "search": (["boiler"], []),
    "fetch": (["engram:123"], []),
}


def _cli_argument_cases() -> list[list[str]]:
    """Exercise defaults, optional values and alternative input sources.

    :returns: command lines for the named tools
    """
    cases = []
    for tool, (required, optional) in TOOL_ARGUMENT_CASES.items():
        command = tool.replace("_", "-")
        reason = (
            [] if tool in {"search", "fetch"} else ["--reason", "Check the contract."]
        )
        cases.extend([[command, *required], [command, *required, *optional, *reason]])
    return cases + [
        ["forget", "personal", "--file", "attachments/manual", "--confirm", "token"],
        [
            "log",
            "personal",
            "training-log.md",
            "--heading",
            "Day 2",
            "--entry-file",
            "{text}",
        ],
        [
            "correct",
            "personal",
            "notes.md",
            "--old-text-file",
            "{text}",
            "--new-text-file",
            "{text}",
            "--message",
            "fix",
        ],
        [
            "share",
            "notes.md",
            "household",
            "--section-file",
            "{text}",
            "--to-path",
            "shared.md",
        ],
        ["instruct", "--body-file", "{text}"],
    ]


def test_schema_cases_cover_every_tool_command_option():
    """Every tool option except automatic help is exercised by a schema case."""
    subparsers = next(
        action
        for action in build_parser()._actions
        if hasattr(action, "choices") and isinstance(action.choices, dict)
    )
    cases = _cli_argument_cases()
    for tool in TOOL_ARGUMENT_CASES:
        command = tool.replace("_", "-")
        declared = {
            option
            for action in subparsers.choices[command]._actions
            if action.dest != "help"
            for option in action.option_strings
        }
        exercised = {
            arg
            for argv in cases
            if argv[0] == command
            for arg in argv[1:]
            if arg.startswith("--")
        }
        assert declared <= exercised, (command, declared - exercised)


@pytest.mark.parametrize("argv", _cli_argument_cases())
async def test_cli_arguments_fit_the_listed_tool_schema(monkeypatch, tmp_path, argv):
    """Validate the arguments the CLI sends against the server's live tool list."""
    async with Client(mcp) as client:
        schemas = {
            listed.name: listed.inputSchema for listed in await client.list_tools()
        }
    assert set(TOOL_ARGUMENT_CASES) == set(schemas)
    file = tmp_path / "manual.pdf"
    file.write_bytes(b"%PDF")
    source = tmp_path / "text.txt"
    source.write_text("Text supplied from a file.")
    paths = {"{file}": str(file), "{text}": str(source)}
    argv = [paths.get(value, value) for value in argv]
    tool = argv[0].replace("-", "_")

    assert await _call(monkeypatch, tmp_path, argv) == 0

    called, arguments = FakeClient.calls[-1]
    assert called == tool
    validate(arguments, schemas[called])
    if tool == "forget" and "--file" in argv:
        assert arguments["file"] == argv[argv.index("--file") + 1]
        assert "engram" not in arguments


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


async def test_open_passes_a_page_of_a_file_through(monkeypatch, tmp_path):
    """A file ref that ends in ``#page=N`` reaches the server as it was typed."""
    status = await _call(
        monkeypatch, tmp_path, ["open", "personal/file/attachments/k#page=3"], [{}]
    )

    assert status == 0
    assert FakeClient.calls[-1] == (
        "open",
        {"reason": "", "refs": ["personal/file/attachments/k#page=3"]},
    )


def test_open_help_says_how_to_read_a_file_from_a_page(capsys):
    """``reef open --help`` names the ``#page=N`` ending of a file ref."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["open", "--help"])
    assert "#page=N" in capsys.readouterr().out


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


TOKEN_URL = "https://example.test/mcp"

#: What the token endpoint answers to a refresh.
FRESH = {
    "access_token": "fresh-access",
    "token_type": "Bearer",
    "expires_in": 3600,
    "refresh_token": "fresh-refresh",
}


async def _sign_in(
    config: Path, *, expires_in: int | None, refresh: str | None
) -> None:
    """Cache a sign-in for the example endpoint, as ``reef login`` leaves it.

    :param config: the CLI's config folder
    :param expires_in: seconds the access token has left, or None for no expiry
    :param refresh: the refresh token, or None for none
    """
    oauth = OAuth(
        mcp_url=TOKEN_URL,
        client_name="Reef CLI",
        token_storage=JsonTokenStore(config / "oauth.json"),
    )
    saved = oauth.token_storage_adapter
    await saved.set_tokens(
        OAuthToken(
            access_token="cached-access",
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=refresh,
        )
    )
    await saved.set_client_info(
        OAuthClientInformationFull(
            client_id="reef-cli",
            client_secret="cli-secret",
            redirect_uris=["http://localhost:9/callback"],
            token_endpoint_auth_method="client_secret_post",
        )
    )


async def _token(
    monkeypatch: Any,
    tmp_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    argv: list[str],
) -> int:
    """Run ``reef token`` against a mocked token endpoint, with no browser.

    A token in ``REEF_ACCESS_TOKEN`` is set too: ``token`` must ignore it.

    :param monkeypatch: pytest's monkeypatch
    :param tmp_path: holds the CLI's config folder, ``config``
    :param handler: answers every request the command makes
    :param argv: the command line after ``reef``
    :returns: the exit status
    """

    def no_browser(url: str, *args: object, **kwargs: object) -> bool:
        """Fail the test: ``reef token`` must never start a sign-in.

        :param url: the page the browser would open
        :param args: ignored
        :param kwargs: ignored
        :raises AssertionError: always
        """
        raise AssertionError(f"reef token opened a browser at {url}")

    monkeypatch.setattr(webbrowser, "open", no_browser)
    monkeypatch.setenv("REEF_ACCESS_TOKEN", "shell-token")
    monkeypatch.setenv("REEF_CONFIG_DIR", str(tmp_path / "config"))
    args = build_parser().parse_args(argv)
    return await run(args, transport=httpx.MockTransport(handler))


def _refuse(request: httpx.Request) -> httpx.Response:
    """Fail the test: no request was expected.

    :param request: the request
    :raises AssertionError: always
    """
    raise AssertionError(f"unexpected request to {request.url}")


async def test_token_prints_the_cached_token(monkeypatch, tmp_path, capsys):
    """A valid cached token is printed alone, and nothing is sent."""
    await _sign_in(tmp_path / "config", expires_in=3600, refresh="old-refresh")

    status = await _token(monkeypatch, tmp_path, _refuse, ["--url", TOKEN_URL, "token"])

    out = capsys.readouterr()
    assert status == 0
    assert out.out == "cached-access\n"
    assert out.err == ""
    commands = next(
        action
        for action in build_parser()._actions
        if action.__class__.__name__ == "_SubParsersAction"
    )
    helps = {choice.dest: choice.help for choice in commands._choices_actions}
    assert (
        helps["token"] == "print a valid access token for this endpoint (for scripts)"
    )


@pytest.mark.parametrize(
    ("expires_in", "options"),
    [(10, []), (3600, ["--refresh-within", "4000"])],
    ids=["expired", "expires-within-the-margin"],
)
async def test_token_refreshes_through_the_cached_sign_in(
    monkeypatch, tmp_path, capsys, expires_in, options
):
    """A token that runs out within the margin is refreshed, and the new one is kept.

    :param expires_in: seconds the cached token has left
    :param options: extra options for ``reef token``
    """
    await _sign_in(tmp_path / "config", expires_in=expires_in, refresh="old-refresh")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Note the request and hand out a fresh token.

        :param request: the refresh request
        :returns: the new tokens
        """
        requests.append(request)
        return httpx.Response(200, json=FRESH)

    argv = ["--url", TOKEN_URL, "token", *options]
    status = await _token(monkeypatch, tmp_path, handler, argv)

    assert status == 0
    assert capsys.readouterr().out == "fresh-access\n"
    assert [(r.method, str(r.url)) for r in requests] == [
        ("POST", "https://example.test/token")
    ]
    form = parse_qs(requests[0].content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["old-refresh"]
    assert form["client_id"] == ["reef-cli"]
    assert form["client_secret"] == ["cli-secret"]
    # The new token is kept: the next call prints it without asking again.
    status = await _token(monkeypatch, tmp_path, _refuse, ["--url", TOKEN_URL, "token"])
    assert status == 0
    assert capsys.readouterr().out == "fresh-access\n"


async def test_token_keeps_the_refresh_token_the_server_did_not_replace(
    monkeypatch, tmp_path, capsys
):
    """A refresh answer without a refresh token keeps the old one for next time."""
    config = tmp_path / "config"
    await _sign_in(config, expires_in=10, refresh="old-refresh")
    without = {key: value for key, value in FRESH.items() if key != "refresh_token"}

    def handler(request: httpx.Request) -> httpx.Response:
        """Hand out a new access token only.

        :param request: the refresh request
        :returns: the new access token
        """
        return httpx.Response(200, json={**without, "expires_in": 10})

    argv = ["--url", TOKEN_URL, "token"]
    assert await _token(monkeypatch, tmp_path, handler, argv) == 0
    oauth = OAuth(
        mcp_url=TOKEN_URL,
        client_name="Reef CLI",
        token_storage=JsonTokenStore(config / "oauth.json"),
    )
    kept = await oauth.token_storage_adapter.get_tokens()
    assert kept is not None and kept.refresh_token == "old-refresh"
    assert capsys.readouterr().out == "fresh-access\n"


def _invalid_grant(request: httpx.Request) -> httpx.Response:
    """Refuse a refresh, as a server does for a revoked sign-in.

    :param request: the refresh request
    :returns: the refusal
    """
    return httpx.Response(400, json={"error": "invalid_grant"})


@pytest.mark.parametrize(
    ("cached", "handler"),
    [
        (None, _refuse),
        ({"expires_in": 10, "refresh": None}, _refuse),
        ({"expires_in": 10, "refresh": "old-refresh"}, _invalid_grant),
    ],
    ids=["nothing-cached", "expired-without-refresh-token", "refresh-refused"],
)
async def test_token_without_a_usable_sign_in(
    monkeypatch, tmp_path, capsys, cached, handler
):
    """No usable sign-in: exit 1 with ``not_signed_in`` on stderr, and no browser.

    :param cached: the sign-in to cache first, or None for none
    :param handler: answers the token endpoint
    """
    if cached is not None:
        await _sign_in(tmp_path / "config", **cached)

    status = await _token(monkeypatch, tmp_path, handler, ["--url", TOKEN_URL, "token"])

    out = capsys.readouterr()
    assert status == 1
    assert out.out == ""
    assert json.loads(out.err) == {"error": "not_signed_in"}


async def test_token_says_when_the_server_fails(monkeypatch, tmp_path, capsys):
    """A failing token endpoint is a server error, and nothing it sent is echoed."""
    await _sign_in(tmp_path / "config", expires_in=10, refresh="old-refresh")

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer with something that is not a token.

        :param request: the refresh request
        :returns: a cut-off body that names a token
        """
        return httpx.Response(200, text='{"access_token": "half-a-token", ')

    status = await _token(monkeypatch, tmp_path, handler, ["--url", TOKEN_URL, "token"])

    out = capsys.readouterr()
    assert status == 1
    assert out.out == ""
    assert json.loads(out.err) == {"error": "server_error"}
    assert "half-a-token" not in out.err


@pytest.mark.parametrize("value", ["-1", "nan", "-inf"])
def test_token_refuses_a_negative_margin(capsys, value):
    """``--refresh-within`` takes no negative number and no NaN.

    :param value: what is given
    """
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["token", f"--refresh-within={value}"])
    assert caught.value.code == 2
    assert "--refresh-within" in capsys.readouterr().err
    zero = build_parser().parse_args(["token", "--refresh-within", "0"])
    assert zero.refresh_within == 0
