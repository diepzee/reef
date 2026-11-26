"""Exercise the served MCP transport; diagnostics must contain only a category."""

import json
import logging
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server.transport_security import TransportSecurityMiddleware

from reef import server, telemetry
from reef.config import get_settings

LOGGER = "reef.transport"
CONTENT = "The bicycle is stored beside the blue cupboard"
IDENTIFIER = "client-request-482"
TOKEN = "Bearer ordinary-test-credential-719"
VERSION = "unsupported-client-version-643"
HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
    "authorization": TOKEN,
}
PING = {"jsonrpc": "2.0", "id": IDENTIFIER, "method": "ping"}


@pytest.fixture(params=[False, True], ids=["local-logs", "with-telemetry"])
def transport(request, monkeypatch, captured, caplog):
    """Use the production HTTP startup wiring, with an in-memory exporter."""
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setattr(get_settings().dev, "insecure", True)
    monkeypatch.setattr(telemetry, "configure", lambda: request.param)
    monkeypatch.setattr(telemetry, "_configured", request.param)
    monkeypatch.setattr(telemetry, "instrument_clients", lambda: None)
    options = {}
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: options.update(kwargs))
    logger = logging.getLogger(LOGGER)
    handlers = logger.handlers[:]
    caplog.set_level(logging.WARNING, logger=LOGGER)
    try:
        server.main()
        server.main()  # repeated installation must not double the diagnostic
        app = server.mcp.http_app(
            path=options["path"], middleware=options["middleware"]
        )

        @asynccontextmanager
        async def connect():
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app),
                    base_url="http://localhost",
                    headers=HEADERS,
                ) as client,
            ):
                yield client

        yield connect
    finally:
        logger.handlers[:] = handlers


async def initialize(client):
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": IDENTIFIER,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "ordinary-test-client", "version": "1"},
            },
        },
    )
    assert response.status_code == 200
    session = response.headers["mcp-session-id"]
    client.headers["mcp-session-id"] = session
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
    )
    assert response.status_code == 202
    return session


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing_session_id", -32600),
        ("unsupported_protocol_version", -32600),
        ("unparseable_json", -32700),
        ("invalid_json_rpc", -32602),
    ],
)
async def test_rejection_is_one_reason_only_diagnostic(
    transport,
    captured,
    caplog,
    case,
    code,
    monkeypatch,
):
    async with transport() as client:
        session = await initialize(client)
        payload = {**PING, "params": {"arguments": {"note": CONTENT}}}
        if case == "missing_session_id":
            del client.headers["mcp-session-id"]
        elif case == "unsupported_protocol_version":
            client.headers["mcp-protocol-version"] = VERSION
        elif case == "invalid_json_rpc":
            payload["jsonrpc"] = CONTENT
        body = json.dumps(payload)
        if case == "unparseable_json":
            body = body[:-1]
        caplog.clear()
        captured.spans.clear()
        response = await client.post("/mcp", content=body)
        assert response.status_code == 400
        error = response.json()
        assert error["jsonrpc"] == "2.0"
        assert error["id"] == "server-error"
        assert error["error"]["code"] == code
        assert response.headers["content-type"] == "application/json"
        if case == "missing_session_id":
            assert error["error"]["message"] == "Bad Request: Missing session ID"
        elif case == "unsupported_protocol_version":
            assert VERSION in error["error"]["message"]
        elif case == "unparseable_json":
            assert error["error"]["message"].startswith("Parse error: ")
        else:
            assert error["error"]["message"].startswith("Validation error: ")
            assert CONTENT in error["error"]["message"]

        records = [r for r in caplog.records if r.name == LOGGER]
        assert len(records) == 1
        [record] = records
        assert record.getMessage() == f"MCP request rejected: {case}"
        assert record.args == (case,)
        assert record.exc_info is None
        assert record.stack_info is None
        diagnostics = [
            s for s in captured.spans if s.name.startswith("MCP request rejected:")
        ]
        assert len(diagnostics) == int(telemetry.is_configured())
        if diagnostics:
            parent = next(
                span
                for span in captured.spans
                if span.context.span_id == diagnostics[0].parent.span_id
            )
            assert parent.attributes["http.status_code"] == 400
        blob = repr(vars(record)) + repr(
            [(s.name, dict(s.attributes or {}), s.events) for s in diagnostics]
        )
        for private in (
            CONTENT,
            IDENTIFIER,
            TOKEN,
            TOKEN.removeprefix("Bearer "),
            VERSION,
            session,
            body,
            "Validation error",
            "input_value",
            "Parse error",
            response.headers["mcp-session-id"],
        ):
            assert private not in blob
        assert case in blob  # usable even with production scrubbing enabled

        # Compare the actual wire response with the unmodified SDK factory.
        # Missing-session requests create random session IDs on both runs.
        original = StreamableHTTPServerTransport._create_error_response.__wrapped__
        with monkeypatch.context() as patch:
            patch.setattr(
                StreamableHTTPServerTransport, "_create_error_response", original
            )
            baseline = await client.post("/mcp", content=body)
        assert response.status_code == baseline.status_code
        assert response.content == baseline.content
        assert {k: v for k, v in response.headers.items() if k != "mcp-session-id"} == {
            k: v for k, v in baseline.headers.items() if k != "mcp-session-id"
        }
        assert bool(response.headers.get("mcp-session-id")) == bool(
            baseline.headers.get("mcp-session-id")
        )
        if case != "missing_session_id":
            assert (
                response.headers["mcp-session-id"] == baseline.headers["mcp-session-id"]
            )
        # The original session still handles a successful request after rejection.
        client.headers["mcp-session-id"] = session
        client.headers.pop("mcp-protocol-version", None)
        assert (await client.post("/mcp", json=PING)).status_code == 200
        assert len([r for r in caplog.records if r.name == LOGGER]) == 1


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize("case", ["missing_session_id", "unsupported_protocol_version"])
async def test_header_rejections_also_cover_get_and_delete(
    transport, caplog, method, case
):
    async with transport() as client:
        await initialize(client)
        if case == "missing_session_id":
            del client.headers["mcp-session-id"]
        else:
            client.headers["mcp-protocol-version"] = VERSION
        response = await client.request(method, "/mcp")
        assert response.status_code == 400
        records = [r for r in caplog.records if r.name == LOGGER]
        assert [r.getMessage() for r in records] == [f"MCP request rejected: {case}"]


async def test_success_and_other_statuses_are_not_rejection_diagnostics(
    transport,
    captured,
    caplog,
):
    async with transport() as client:
        await initialize(client)
        response = await client.post("/mcp", json=PING)
        assert response.status_code == 200
        assert '"result":{}' in response.text
        response = await client.post(
            "/mcp", json=PING, headers={"accept": "text/plain"}
        )
        assert response.status_code == 406
        response = await client.post(
            "/mcp", json=PING, headers={"mcp-session-id": "expired-832"}
        )
        assert response.status_code == 404
        assert not [r for r in caplog.records if r.name == LOGGER]
        assert not [
            s for s in captured.spans if s.name.startswith("MCP request rejected:")
        ]


async def test_a_client_can_initialize_after_a_missing_session_rejection(
    transport, caplog
):
    """A possible reconnect sequence, not evidence of what Claude sent."""
    async with transport() as client:
        rejected = await client.post("/mcp", json=PING)
        assert rejected.status_code == 400
        await initialize(client)
        accepted = await client.post("/mcp", json=PING)
        assert accepted.status_code == 200
        records = [r for r in caplog.records if r.name == LOGGER]
        assert [r.getMessage() for r in records] == [
            "MCP request rejected: missing_session_id"
        ]


@pytest.mark.parametrize("content_type", [None, "text/plain; note=ordinary-header-472"])
@pytest.mark.parametrize("with_session", [False, True])
async def test_content_type_rejection_is_one_safe_diagnostic(
    transport,
    captured,
    caplog,
    monkeypatch,
    content_type,
    with_session,
):
    async with transport() as client:
        session = await initialize(client) if with_session else None
        client.headers.pop("content-type")
        if content_type is not None:
            client.headers["content-type"] = content_type
        caplog.clear()
        captured.spans.clear()
        # Also malformed JSON: security rejects first, with just one category.
        body = json.dumps({**PING, "params": {"note": CONTENT}})[:-1]
        response = await client.post("/mcp", content=body)
        assert response.status_code == 400
        assert response.content == b"Invalid Content-Type header"
        records = [r for r in caplog.records if r.name == LOGGER]
        assert len(records) == 1
        [record] = records
        assert record.getMessage() == "MCP request rejected: invalid_content_type"
        assert record.args == ("invalid_content_type",)
        assert record.exc_info is None
        assert record.stack_info is None
        diagnostics = [
            s for s in captured.spans if s.name.startswith("MCP request rejected:")
        ]
        assert len(diagnostics) == int(telemetry.is_configured())
        blob = repr(vars(record)) + repr(
            [(s.name, dict(s.attributes or {}), s.events) for s in diagnostics]
        )
        for private in (
            CONTENT,
            IDENTIFIER,
            TOKEN,
            TOKEN.removeprefix("Bearer "),
            "ordinary-header-472",
            body,
            "Invalid Content-Type header",
        ):
            assert private not in blob
        if session:
            assert session not in blob
        original = TransportSecurityMiddleware.validate_request.__wrapped__
        with monkeypatch.context() as patch:
            patch.setattr(TransportSecurityMiddleware, "validate_request", original)
            baseline = await client.post("/mcp", content=body)
        assert (response.status_code, response.content, response.headers) == (
            baseline.status_code,
            baseline.content,
            baseline.headers,
        )
        assert len([r for r in caplog.records if r.name == LOGGER]) == 1
