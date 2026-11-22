"""Read what Codex did from the events it prints with ``codex exec --json``.

Tool use is read from this record, never from the assistant's own account
of it. Codex 0.167 prints one JSON object per line: ``thread.started`` with
the session's ``thread_id``; ``item.started``, ``item.updated`` and
``item.completed`` with an ``item``; and ``turn.completed`` or
``turn.failed`` at the end. An item's ``type`` says what it is:
``agent_message`` (text to the person), ``reasoning``, ``mcp_tool_call``
(``server``, ``tool``, ``arguments``, ``result``, ``error``, ``status``),
``command_execution``, ``file_change``, ``web_search``, ``todo_list`` or
``error``. ``tests/conversations/sample-events.jsonl`` is a real record.

A call to a Reef server is a :class:`ToolCall`. Anything else the assistant
used to act or to learn, a shell command, a file edit, a web search or
another server's tool, is listed in ``other_tools``: a conversation that
used one did not rely on Reef alone.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace

#: The names Reef's server goes by in the runner's Codex settings.
REEF_SERVERS = frozenset({"reef_local", "reef_test"})

#: Items that are neither a tool nor a source of information: the
#: assistant's words, its reasoning, its own to-do list, and warnings.
_NOT_TOOLS = frozenset({"agent_message", "reasoning", "todo_list", "error"})

#: Short names for the tools Codex has besides MCP servers.
_OTHER_NAMES = {"command_execution": "shell"}


@dataclass(frozen=True)
class ToolCall:
    """One call to a Reef tool.

    :param server: the MCP server's name in the Codex settings
    :param tool: the tool's name
    :param arguments: the arguments the assistant sent
    :param ok: whether the call finished without an error
    :param turn: which message of the conversation the call answered, from 0
    :param output: the text the call returned, kept for ``load_index`` only,
        to see whose memory it is
    """

    server: str
    tool: str
    arguments: dict
    ok: bool
    turn: int = 0
    output: str = ""


@dataclass(frozen=True)
class Session:
    """What happened in one Codex conversation.

    :param session_id: Codex's id for the conversation, to resume it
    :param tool_calls: the calls to Reef, in order
    :param other_tools: every other tool used, in order
    :param answer: the assistant's last message; for several turns, the
        last message of each turn
    :param error: why Codex stopped, when a turn failed or did not finish
    """

    session_id: str
    tool_calls: list[ToolCall]
    other_tools: list[str]
    answer: str
    error: str | None = None


def parse_events(lines: Iterable[str]) -> Session:
    """Read one ``codex exec --json`` run.

    Lines that are not JSON are skipped. An item is read in its last state,
    so a call that started but never completed counts as failed.

    :param lines: the run's standard output, line by line
    :returns: the session
    """
    session_id = ""
    items: dict[str, dict] = {}
    finished = False
    failure: str | None = None
    last_error: str | None = None
    for line in lines:
        event = _decode(line)
        if event is None:
            continue
        kind = event.get("type")
        if kind == "thread.started":
            session_id = session_id or str(event.get("thread_id") or "")
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item") or {}
            key = str(item.get("id", len(items)))
            items[key] = {**items.get(key, {}), **item}
        elif kind == "turn.completed":
            finished = True
        elif kind == "turn.failed":
            failure = _message(event.get("error")) or "the turn failed"
        elif kind == "error":
            last_error = _message(event)
    tool_calls: list[ToolCall] = []
    other_tools: list[str] = []
    answer = ""
    for item in items.values():
        kind = item.get("type", "")
        if kind == "agent_message":
            answer = str(item.get("text") or "")
        elif kind == "mcp_tool_call":
            call = _tool_call(item)
            if call.server in REEF_SERVERS:
                tool_calls.append(call)
            else:
                other_tools.append(f"{call.server}.{call.tool}")
        elif kind not in _NOT_TOOLS:
            other_tools.append(_OTHER_NAMES.get(kind, kind))
    if failure is None and not finished:
        failure = last_error or "the turn did not finish"
    return Session(session_id, tool_calls, other_tools, answer, failure)


def combine(sessions: list[Session]) -> Session:
    """Join the turns of one conversation into one session.

    :param sessions: each turn's session, in order; at least one
    :returns: the calls and tools of every turn in order, each turn's answer
        separated by a blank line, and the first error
    """
    errors = [session.error for session in sessions if session.error]
    return Session(
        session_id=sessions[0].session_id,
        tool_calls=[
            replace(call, turn=turn)
            for turn, session in enumerate(sessions)
            for call in session.tool_calls
        ],
        other_tools=[tool for session in sessions for tool in session.other_tools],
        answer="\n\n".join(session.answer for session in sessions if session.answer),
        error=errors[0] if errors else None,
    )


def _decode(line: str) -> dict | None:
    """Decode one line, or None when it is not a JSON object.

    :param line: the line
    :returns: the event
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _message(source: object) -> str | None:
    """Take the message out of an error event or an error object.

    :param source: the event or the object
    :returns: its ``message``, if it has one
    """
    if isinstance(source, dict) and source.get("message"):
        return str(source["message"])
    return None


def _tool_call(item: dict) -> ToolCall:
    """Read an MCP call from its item.

    A call is ok when it completed with no error and the server did not mark
    its result as an error.

    :param item: the ``mcp_tool_call`` item, in its last state
    :returns: the call
    """
    result = item.get("result")
    if not isinstance(result, dict):
        result = {}
    refused = bool(result.get("isError") or result.get("is_error"))
    ok = item.get("status") == "completed" and not item.get("error") and not refused
    arguments = item.get("arguments")
    tool = str(item.get("tool", ""))
    texts = [
        str(part.get("text", ""))
        for part in result.get("content") or []
        if isinstance(part, dict)
    ]
    return ToolCall(
        server=str(item.get("server", "")),
        tool=tool,
        arguments=arguments if isinstance(arguments, dict) else {},
        ok=ok,
        output="\n".join(texts) if tool == "load_index" else "",
    )
