"""Only async shells are safe for the gate to validate without running."""

import inspect
from collections.abc import Callable

import pytest
from fastmcp import FastMCP

from reef import server


@pytest.mark.parametrize(
    "register", [server._read_only, server._additive, server._destructive]
)
def test_a_sync_shell_is_refused(
    monkeypatch: pytest.MonkeyPatch, register: Callable
) -> None:
    """Refuse a sync shell before adding it to the tools or the gate.

    :param monkeypatch: isolate registration from the real server
    :param register: one of the tool registration decorators
    """
    monkeypatch.setattr(server, "mcp", FastMCP("registration"))
    monkeypatch.setattr(server, "_ARGUMENTS", {})

    def sync_shell() -> dict:
        """Stand in for a shell whose body validation must never run.

        :returns: the tool answer
        """
        return {}

    with pytest.raises(AssertionError, match="shell must be async"):
        register("Sync shell")(sync_shell)
    assert server._ARGUMENTS == {}


async def test_every_registered_shell_is_async() -> None:
    """Check every shell the gate can call without executing its body."""
    tools = await server.mcp.list_tools()
    assert tools
    assert all(inspect.iscoroutinefunction(tool.fn) for tool in tools)
