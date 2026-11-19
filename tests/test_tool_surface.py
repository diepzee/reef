"""The tools an assistant sees: sixteen workflow methods, through a real client.

Every test here talks to the server the way an assistant does, through
FastMCP's own MCP client connected in process, so what is checked is what a
client lists and receives: names, input schemas, annotations, results and
errors. Calling the shell functions from Python would skip the protocol,
which is the part these tests are about.

The client signs in as the development principal, as stdio does.
"""

import inspect
import json
import logging
from collections.abc import AsyncIterator

import pydantic_core
import pytest
import pytest_asyncio
from conftest import SpanCapture
from fastmcp import Client
from opentelemetry.trace import SpanKind

from reef import server
from reef.access import AccessDenied, Principal, arm
from reef.attachments import add_attachment
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import save_engram
from reef.methods import MESSAGES, REGISTRY, find
from reef.models import CallLog
from reef.server import mcp

#: The sixteen tools, exactly.
SIXTEEN = {
    "load_index",
    "recall",
    "brief",
    "open",
    "catch_up",
    "remember",
    "log",
    "write",
    "store_file",
    "correct",
    "undo",
    "forget",
    "share",
    "instruct",
    "search",
    "fetch",
}

#: The two tools whose schemas the connector contract fixes: no reason.
CONNECTOR = {"search", "fetch"}

#: Tools that change nothing, so a client may run them without asking.
READ_ONLY = {"load_index", "recall", "brief", "open", "catch_up", "search", "fetch"}

#: Tools that may change or remove what is already there.
DESTRUCTIVE = {"write", "correct", "undo", "forget", "share", "instruct"}

#: The website, where people manage spaces and members themselves.
WEBSITE = "Reef website"

#: Every tool that was retired, with what its answer must name.
RETIRED = {
    "get_operating_protocol": "load_index",
    "list_coves": "load_index",
    "load_all_context": "open",
    "search_pages": "recall",
    "read_page": "open",
    "read_pages": "open",
    "read_file": "open",
    "read_image": "open",
    "whats_new": "catch_up",
    "write_page": "write",
    "write_pages": "write",
    "add_file": "store_file",
    "add_image": "store_file",
    "edit_page_section": "correct",
    "delete_page": "forget",
    "delete_file": "forget",
    "delete_image": "forget",
    "prepare_to_share": "share",
    "confirm_share": "share",
    "update_meta_page": "instruct",
    "create_cove": WEBSITE,
    "rename_cove": WEBSITE,
    "delete_cove": WEBSITE,
    "leave_cove": WEBSITE,
    "invite": WEBSITE,
    "invite_to_reef": WEBSITE,
    "remove_member": WEBSITE,
}

#: The input schemas a connector limited to ``search`` and ``fetch`` was
#: given before the tools changed, as listed then. Strict connectors check
#: them, so they stay exactly as they were.
SEARCH_INPUT = {
    "additionalProperties": False,
    "properties": {"query": {"type": "string", "description": "words to search for"}},
    "required": ["query"],
    "type": "object",
}
FETCH_INPUT = {
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "description": "an id from a `search` result"}
    },
    "required": ["id"],
    "type": "object",
}
CONNECTOR_OUTPUT = {"additionalProperties": True, "type": "object"}

BASE = "https://reef.example"


def principal_for(person) -> Principal:
    """Build the principal for a seeded person.

    :param person: a seeded person
    :returns: the principal
    """
    return Principal(person_id=person.id, email=person.email)


@pytest_asyncio.fixture
async def client(household, monkeypatch) -> AsyncIterator[Client]:
    """Connect an MCP client to the server in process, signed in as Wouter.

    :param household: two people sharing a household space
    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the connected client
    """
    monkeypatch.setattr(
        get_settings().dev, "principal_email", household["wouter"].email
    )
    monkeypatch.setattr(get_settings().web, "base_url", BASE)
    async with Client(mcp) as connected:
        yield connected


async def _listed(client: Client) -> dict:
    """List the tools as the client sees them, by name.

    :param client: the connected client
    :returns: each listed tool, by name
    """
    return {tool.name: tool for tool in await client.list_tools()}


async def _error(client: Client, name: str, arguments: dict) -> str:
    """Call a tool that must fail, and return what the client reads.

    :param client: the connected client
    :param name: the tool
    :param arguments: its arguments
    :returns: the error's text
    """
    result = await client.call_tool(name, arguments, raise_on_error=False)
    assert result.is_error, f"{name} did not fail"
    return "".join(block.text for block in result.content)


# --- what is listed --------------------------------------------------------------


async def test_exactly_sixteen_tools(client):
    """The sixteen workflow methods are listed, and nothing else."""
    assert set(await _listed(client)) == SIXTEEN


async def test_reason_is_required_except_search_and_fetch(client):
    """Every method but the connector pair asks why; the pair keep their schemas.

    The listing says the reason is required, so assistants send one, though
    a call without it still works (see below).
    """
    for name, tool in (await _listed(client)).items():
        schema = tool.inputSchema
        if name in CONNECTOR:
            assert "reason" not in schema["properties"], name
            continue
        assert "reason" in schema["required"], name
        reason = schema["properties"]["reason"]
        assert reason["type"] == "string", name
        assert "maxLength" not in reason, f"{name}: a long reason is cut, not refused"
        assert "default" not in reason, f"{name}: the reason is listed as required"
        assert "privately" in reason["description"], name
        assert "improve Reef" in reason["description"], name
    listed = await _listed(client)
    assert listed["search"].inputSchema == SEARCH_INPUT
    assert listed["fetch"].inputSchema == FETCH_INPUT


async def test_caller_is_absent_from_every_input_schema(client):
    """Who calls comes from the connection, never from the arguments."""
    for name, tool in (await _listed(client)).items():
        assert "caller" not in tool.inputSchema["properties"], name
        assert "caller" not in json.dumps(tool.inputSchema), name


async def test_input_schema_is_the_methods_own_arguments(client):
    """Each tool takes what its method takes, with the same ones required."""
    for name, tool in (await _listed(client)).items():
        parameters = list(inspect.signature(REGISTRY[name]).parameters.values())[1:]
        if name in CONNECTOR:
            parameters = [p for p in parameters if p.name != "reason"]
        names = [p.name for p in parameters]
        required = {p.name for p in parameters if p.default is inspect.Parameter.empty}
        assert list(tool.inputSchema["properties"]) == names, name
        assert set(tool.inputSchema.get("required", [])) == required, name


async def test_annotations(client):
    """Reads are marked read-only; forget and the other overwrites are destructive.

    Clients decide from these hints whether a call may run without asking,
    and a directory rejects a server whose tools lack a title.
    """
    for name, tool in (await _listed(client)).items():
        hints = tool.annotations
        assert hints is not None and hints.title, f"{name} has no title"
        assert len(name) <= 64, name
        if name in READ_ONLY:
            assert hints.readOnlyHint is True, f"{name} is not read-only"
            assert hints.destructiveHint is False, name
        else:
            assert hints.readOnlyHint is False, f"{name} claims read-only"
            assert hints.destructiveHint is (name in DESTRUCTIVE), name
    assert (await _listed(client))["forget"].annotations.destructiveHint is True


# --- old names -------------------------------------------------------------------


async def test_old_tool_name_names_its_replacement(client):
    """A call by an old name is an error that says what to use instead."""
    text = await _error(client, "read_pages", {"cove": "personal", "paths": ["a.md"]})
    assert "open" in text


@pytest.mark.parametrize(("old", "replacement"), sorted(RETIRED.items()))
async def test_every_old_name_is_unlisted_and_answers(client, old, replacement):
    """Each old name that did not live on is unlisted, and points the way."""
    assert old not in await _listed(client)
    text = await _error(client, old, {})
    assert replacement in text
    assert old in text


async def test_an_unknown_name_is_still_unknown(client):
    """Only the old names get an answer; anything else is FastMCP's own refusal."""
    text = await _error(client, "no_such_tool", {})
    assert "Unknown tool" in text


# --- search and fetch ------------------------------------------------------------


async def test_search_and_fetch_shapes_unchanged(client, household, monkeypatch):
    """The connector pair answer exactly as before, byte for byte.

    The ids, titles, URLs and metadata keep their old words (``page``,
    ``cove``): a connector may hold ids from before, and changing them would
    break it.
    """
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_engram(
            me,
            "household",
            "boiler.md",
            "The boiler is a Vaillant.",
            message="x",
            title="Boiler",
        )
    listed = await _listed(client)
    for name in CONNECTOR:
        assert listed[name].outputSchema == CONNECTOR_OUTPUT, name

    found = await client.call_tool("search", {"query": "Vaillant"})
    expected = {
        "results": [
            {
                "id": "protocol:operating-protocol",
                "title": "How to use this memory — read before answering",
                "url": f"{BASE}/docs",
            },
            {
                "id": "page:household/boiler.md",
                "title": "Boiler",
                "url": f"{BASE}/app/s/household/p/boiler.md",
            },
        ]
    }
    assert found.structured_content == expected
    assert found.content[0].text == pydantic_core.to_json(expected).decode()

    fetched = await client.call_tool("fetch", {"id": "page:household/boiler.md"})
    expected = {
        "id": "page:household/boiler.md",
        "title": "Boiler",
        "text": "The boiler is a Vaillant.",
        "url": f"{BASE}/app/s/household/p/boiler.md",
        "metadata": {"cove": "household", "path": "boiler.md", "kind": "page"},
    }
    assert fetched.structured_content == expected
    assert fetched.content[0].text == pydantic_core.to_json(expected).decode()

    for identifier, answer in [
        ("page:household/nope.md", {"error": "not_found"}),
        ("nonsense", {"error": "bad_id", "detail": "not an id returned by search"}),
    ]:
        result = await client.call_tool("fetch", {"id": identifier})
        assert result.is_error is False
        assert result.structured_content == answer


async def test_fetch_answers_a_file_in_its_old_shape(client, household):
    """A file is fetched as its description, under the same old words."""

    class Store:
        """An object store that keeps nothing."""

        async def put(self, key: str, data: bytes, mime: str) -> None:
            """Discard the bytes."""

    me = principal_for(household["wouter"])
    attachment = await add_attachment(
        me,
        "household",
        b"%PDF",
        "application/pdf",
        filename="lease.pdf",
        description="The lease of the house.",
        store=Store(),
    )
    key = attachment.object_key
    found = await client.call_tool("search", {"query": "lease"})
    assert found.structured_content["results"][1] == {
        "id": f"file:household/{key}",
        "title": "lease.pdf",
        "url": f"{BASE}/app/s/household/p/",
    }
    fetched = await client.call_tool("fetch", {"id": f"file:household/{key}"})
    assert fetched.structured_content == {
        "id": f"file:household/{key}",
        "title": "lease.pdf",
        "text": "The lease of the house.",
        "url": f"{BASE}/app/s/household/p/",
        "metadata": {"cove": "household", "kind": "file", "mime": "application/pdf"},
    }


# --- errors ----------------------------------------------------------------------


async def test_a_method_error_reaches_the_client_with_message_and_code(client):
    """A refusal is a tool error: the plain message, then the code in brackets."""
    text = await _error(
        client,
        "open",
        {"reason": "", "refs": ["personal/house.md"], "as_of": "last March"},
    )
    assert text == f"{MESSAGES['invalid_as_of']} [invalid_as_of]"


async def test_a_library_exception_message_never_reaches_the_client(
    client, monkeypatch, caplog
):
    """Only a fixed message goes out, and only the error's class is logged.

    A library's own message can name a person or an address, as this one
    does.
    """

    async def denied(principal):
        """Fail as the access layer does, naming an address."""
        raise AccessDenied("not on the allowlist: secret@example.test")

    monkeypatch.setattr(find, "build_index", denied)
    with caplog.at_level(logging.DEBUG):
        text = await _error(client, "load_index", {"reason": "start"})
    assert text == "Reef could not do that just now."
    assert "AccessDenied" in caplog.text
    assert "secret@example.test" not in caplog.text


async def test_a_failure_to_know_the_caller_is_plain_too(client, monkeypatch, caplog):
    """An unknown principal is told nothing about who it was taken for."""

    async def unknown():
        """Fail as principal resolution does, naming an address."""
        raise AccessDenied("unknown principal: secret@example.test")

    monkeypatch.setattr(server, "current_principal", unknown)
    with caplog.at_level(logging.DEBUG):
        text = await _error(client, "recall", {"reason": "", "question": "boiler"})
    assert text == "Reef could not do that just now."
    assert "secret@example.test" not in caplog.text


def _traced(sink: SpanCapture) -> str:
    """Flatten every span the server exported into one searchable string.

    Names, attributes, events and status, so a value that leaks through any
    of them is found. The test's own client runs in this process and traces
    its side of each call too, with what it was told; in production the
    client is somebody else's program, so its spans are left out.

    :param sink: the capturing exporter
    :returns: all of it, as text
    """
    return "".join(
        f"{span.name}{dict(span.attributes or {})}{span.status.description}"
        + "".join(
            f"{event.name}{dict(event.attributes or {})}" for event in span.events
        )
        for span in sink.spans
        if span.kind is not SpanKind.CLIENT
    )


def _logged(caplog: pytest.LogCaptureFixture) -> str:
    """Return every record logged, formatted, with tracebacks.

    :param caplog: pytest's log capture, set to DEBUG
    :returns: all of it, as text
    """
    return caplog.text + "".join(record.getMessage() for record in caplog.records)


@pytest.fixture
def every_log(caplog) -> pytest.LogCaptureFixture:
    """Capture every record at DEBUG, FastMCP's and the MCP SDK's included.

    FastMCP's loggers do not propagate to the root, so each is set apart.

    :param caplog: pytest's log capture
    :returns: the capture
    """
    for name in (None, "fastmcp", "mcp", "reef"):
        caplog.set_level(logging.DEBUG, logger=name)
    return caplog


async def test_a_refusal_leaves_no_message_on_the_trace(client, captured):
    """What a refusal says stays between Reef and the client.

    FastMCP records an exception raised by a tool on its trace span,
    message and all, and the trace goes to the telemetry service. A refusal
    can name an engram's path, so it is returned as an error result rather
    than raised.
    """
    await _error(
        client,
        "open",
        {"reason": "", "refs": ["personal/house.md"], "as_of": "last March"},
    )
    assert any(span.name == "tools/call open" for span in captured.spans)
    assert MESSAGES["invalid_as_of"] not in _traced(captured)


#: A value no log line or span should ever hold.
PRIVATE = "zebra-crossing-7731"


async def test_a_call_without_a_reason_works_and_leaks_nothing(
    client, household, monkeypatch, captured, every_log
):
    """A weaker model that leaves out the reason is not failed for it.

    The call runs, with an empty reason in the call log, and none of its
    arguments reaches a log line or a span.
    """
    monkeypatch.setattr(get_settings().calls, "log", True)
    result = await client.call_tool("recall", {"question": PRIVATE})
    assert result.is_error is False
    async with transaction_scope():
        await arm(principal_for(household["wouter"]))
        [row] = await CallLog.select()
    assert (row["method"], row["reason"]) == ("recall", "")
    leaked = {
        "log": PRIVATE in _logged(every_log),
        "trace": PRIVATE in _traced(captured),
    }
    assert leaked == {"log": False, "trace": False}


@pytest.mark.parametrize(
    ("arguments", "named"),
    [
        ({"reason": "", "question": "boiler", "limit": PRIVATE}, "limit"),
        ({"reason": "", "space": PRIVATE}, "question"),
        ({"reason": "", "question": "boiler", "colour": PRIVATE}, "colour"),
        ({"reason": ["a", PRIVATE], "question": "boiler"}, "reason"),
    ],
)
async def test_bad_arguments_are_refused_by_name_and_leak_nothing(
    client, captured, every_log, arguments, named
):
    """A wrong, missing or unknown argument is named, and no value goes anywhere.

    FastMCP's own refusal quotes every argument's value, in its log and on
    its trace, so these are answered before FastMCP sees them.
    """
    text = await _error(client, "recall", arguments)
    leaked = {
        "log": PRIVATE in _logged(every_log),
        "trace": PRIVATE in _traced(captured),
        "client": PRIVATE in text,
    }
    assert leaked == {"log": False, "trace": False, "client": False}
    assert text.startswith(f"These arguments were missing or not valid: {named}.")
    assert text.endswith("[invalid_arguments]")


async def test_the_connector_pair_refuses_bad_arguments_by_name(client):
    """``search`` and ``fetch`` are checked the same way."""
    text = await _error(client, "fetch", {"id": 7, "extra": PRIVATE})
    assert text.startswith("These arguments were missing or not valid: extra, id.")
    assert PRIVATE not in text


# --- the call log ----------------------------------------------------------------


async def test_a_tool_call_is_logged_as_the_assistant(client, household, monkeypatch):
    """The shell calls the method as the assistant, in the client's session."""
    monkeypatch.setattr(get_settings().calls, "log", True)
    await client.call_tool("recall", {"reason": "which boiler", "question": "boiler"})
    await client.call_tool("search", {"query": "boiler"})
    async with transaction_scope():
        await arm(principal_for(household["wouter"]))
        rows = await CallLog.select().order_by(CallLog.called_at)
    assert [(row["method"], row["reason"]) for row in rows] == [
        ("recall", "which boiler"),
        ("search", ""),
    ]
    assert {row["writer"] for row in rows} == {"assistant"}
    assert all(row["session_id"] for row in rows)
