"""Reason-only diagnostics for the SDK's MCP request rejections.

These happen before FastMCP's tool middleware. Observe the SDK's error
response factory and security result instead of parsing requests a second
time or changing validation order. No request, response text, exception or
identifier is passed to the logger, including in structured attributes.
"""

import logging
from functools import wraps
from http import HTTPStatus

from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server.transport_security import TransportSecurityMiddleware
from mcp.types import INVALID_PARAMS, INVALID_REQUEST, PARSE_ERROR

from reef import telemetry

logger = logging.getLogger("reef.transport")


def log_rejections() -> None:
    """Install once before serving HTTP, forwarding only this safe logger.

    FastMCP has no rejection callback. This private SDK hook, like the body
    limit injection, is covered through the actual transport so an SDK
    upgrade that changes its cases fails the tests. Unknown responses are
    left alone, never logged verbatim or guessed from request content.
    """
    telemetry.forward_logs(logger)
    if getattr(StreamableHTTPServerTransport, "_reef_rejection_logging", False):
        return
    original = StreamableHTTPServerTransport._create_error_response

    @wraps(original)
    def with_diagnostic(
        self, error_message, status_code, error_code=INVALID_REQUEST, headers=None
    ):
        response = original(self, error_message, status_code, error_code, headers)
        category = None
        if status_code == HTTPStatus.BAD_REQUEST:
            if error_code == PARSE_ERROR:
                category = "unparseable_json"
            elif error_code == INVALID_PARAMS:
                category = "invalid_json_rpc"
            elif error_code == INVALID_REQUEST:
                if error_message == "Bad Request: Missing session ID":
                    category = "missing_session_id"
                elif error_message.startswith(
                    "Bad Request: Unsupported protocol version: "
                ):
                    category = "unsupported_protocol_version"
        if category is not None:
            logger.warning("MCP request rejected: %s", category)
        return response

    StreamableHTTPServerTransport._create_error_response = with_diagnostic

    # Security rejects content types before the transport's error factory.
    # Its only 400 is this failure (host/origin failures use 421/403). The
    # transport returns it immediately, so the two hooks cannot report the
    # same rejection. Observe only the status, never headers or body text.
    original_security = TransportSecurityMiddleware.validate_request

    @wraps(original_security)
    async def with_security_diagnostic(self, request, is_post=False):
        response = await original_security(self, request, is_post=is_post)
        if response is not None and response.status_code == HTTPStatus.BAD_REQUEST:
            logger.warning("MCP request rejected: %s", "invalid_content_type")
        return response

    TransportSecurityMiddleware.validate_request = with_security_diagnostic
    StreamableHTTPServerTransport._reef_rejection_logging = True
