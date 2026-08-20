"""Every tool carries the annotations a connector directory review demands.

Anthropic's directory rejects servers whose tools lack a title or the
applicable read-only/destructive hint, and Claude reads those hints to decide
which calls may run without asking the user first — a write mislabelled
read-only is a silent auto-permission over somebody's memory.

The classification therefore lives here as data rather than being inferred
from the annotations under test. A newly added tool that nobody has placed in
one of these three sets fails the suite, which is the point: the failure
arrives before the directory does.
"""

from reef.server import mcp


async def registered_tools() -> dict:
    """Map every tool the server advertises to its registration."""
    return {tool.name: tool for tool in await mcp.list_tools()}


#: Tools that change nothing. ``readOnlyHint`` lets these run unprompted.
READ_ONLY = {
    # The two-tool connector shape. Adapters over search_pages and
    # read_page, so read-only for the same reason those are.
    "fetch",
    "search",
    "get_operating_protocol",
    "list_spaces",
    "load_all_context",
    "load_index",
    "read_file",
    "read_image",
    "read_page",
    "read_pages",
    "search_pages",
    "whats_new",
}

#: Writes that only ever add. Nothing a user holds today is lost to these.
ADDITIVE = {
    "add_file",
    "add_image",
    "create_space",
    "invite",
    "invite_to_reef",
    "prepare_to_share",
    "remember",
    "rename_cove",
}

#: Writes that overwrite, remove, or revoke. ``confirm_share`` belongs here
#: because promoting a page stubs the personal original, not because it
#: deletes anything outright.
DESTRUCTIVE = {
    "confirm_share",
    "delete_file",
    "delete_image",
    "delete_page",
    "delete_space",
    "edit_page_section",
    "leave_space",
    "remove_member",
    "update_meta_page",
    "write_page",
    "write_pages",
}


async def test_every_tool_is_classified():
    """No tool reaches a directory without somebody deciding what it does."""
    registered = set(await registered_tools())
    classified = READ_ONLY | ADDITIVE | DESTRUCTIVE
    assert registered - classified == set(), "unclassified tools"
    assert classified - registered == set(), "classified tools that do not exist"


async def test_every_tool_has_a_title():
    """Some clients drop a tool with no title; a directory review rejects it."""
    for name, tool in (await registered_tools()).items():
        annotations = tool.annotations
        assert annotations is not None, f"{name} has no annotations"
        assert annotations.title, f"{name} has no title"


async def test_read_only_tools_declare_themselves_read_only():
    for name, tool in (await registered_tools()).items():
        if name not in READ_ONLY:
            continue
        assert tool.annotations.readOnlyHint is True, f"{name} is not read-only"


async def test_writing_tools_are_never_marked_read_only():
    for name, tool in (await registered_tools()).items():
        if name in READ_ONLY:
            continue
        assert tool.annotations.readOnlyHint is not True, f"{name} claims read-only"


async def test_destructive_tools_are_flagged_and_additive_ones_are_not():
    """The hint decides whether Claude asks first, so both directions matter."""
    for name, tool in (await registered_tools()).items():
        if name in READ_ONLY:
            continue
        assert tool.annotations.destructiveHint is (name in DESTRUCTIVE), name


async def test_tool_names_fit_the_directory_limit():
    for name in await registered_tools():
        assert len(name) <= 64, name
