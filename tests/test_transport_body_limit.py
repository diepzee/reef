"""The Streamable HTTP transport must accept a maximum-size ``add_file``.

The MCP SDK 413s request bodies over 4 MiB by default, and FastMCP builds
the session manager without exposing that knob — so reef injects the limit
at the SDK base class. These tests pin the injection: the manager FastMCP
actually constructs must come out sized for ``file_max_bytes`` in base64
form, and an explicitly passed limit must still win.
"""

from unittest.mock import Mock

from fastmcp.server.http import FastMCPStreamableHTTPSessionManager
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from reef.config import get_settings
from reef.server import _lift_transport_body_limit, transport_body_limit


def test_limit_fits_the_largest_allowed_file():
    raw = get_settings().file_max_bytes
    limit = transport_body_limit(raw)
    assert limit > raw * 4 // 3


def test_fastmcp_manager_comes_out_sized_for_file_max_bytes():
    _lift_transport_body_limit()
    manager = FastMCPStreamableHTTPSessionManager(app=Mock())
    expected = transport_body_limit(get_settings().file_max_bytes)
    assert manager.max_request_body_size == expected
    assert manager.asgi_app.max_body_size == expected


def test_explicit_limit_still_wins():
    _lift_transport_body_limit()
    manager = StreamableHTTPSessionManager(app=Mock(), max_request_body_size=123)
    assert manager.max_request_body_size == 123


def test_lifting_twice_does_not_stack():
    _lift_transport_body_limit()
    first = StreamableHTTPSessionManager.__init__
    _lift_transport_body_limit()
    assert StreamableHTTPSessionManager.__init__ is first
