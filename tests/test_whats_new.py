"""The activity surface: what changed, where, by whom, since when.

Shared memory rots socially before it rots factually — a cove only feels
alive if members can see what the others' assistants wrote. The same RLS
boundary applies: activity in somebody else's personal cove is invisible,
and the leak test proves it.
"""

from datetime import UTC, datetime

from reef.access import Principal, arm
from reef.activity import whats_new
from reef.models import Attachment, AttachmentStatus
from reef.pages import save_page
from reef.server import tool_whats_new


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_a_partners_shared_edit_is_visible_with_author_and_message(tx, household):
    partner = principal_for(household["partner"])
    await save_page(
        partner,
        "household",
        "house.md",
        "The boiler was serviced.",
        message="Log the service visit",
    )
    me = principal_for(household["wouter"])
    events = await whats_new(me)
    assert len(events) == 1
    event = events[0]
    assert event["cove"] == "household"
    assert event["kind"] == "page"
    assert event["path"] == "house.md"
    assert event["author"] == "Partner"
    assert event["message"] == "Log the service visit"


async def test_own_edits_are_listed_too(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "A note.", message="x")
    events = await whats_new(me)
    assert [(e["cove"], e["author"]) for e in events] == [("personal", "Wouter")]


async def test_anothers_personal_activity_is_invisible(tx, household):
    partner = principal_for(household["partner"])
    await save_page(partner, "personal", "diary.md", "Private.", message="x")
    assert await whats_new(partner) != []
    me = principal_for(household["wouter"])
    assert await whats_new(me) == []


async def test_since_excludes_older_events(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "old.md", "Old news.", message="x")
    cutoff = datetime.now(UTC)
    await save_page(me, "personal", "new.md", "Fresh news.", message="x")
    events = await whats_new(me, since=cutoff)
    assert [e["path"] for e in events] == ["new.md"]


async def test_newest_events_come_first(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "first.md", "One.", message="x")
    await save_page(me, "personal", "second.md", "Two.", message="x")
    events = await whats_new(me)
    assert [e["path"] for e in events] == ["second.md", "first.md"]


async def test_a_new_file_is_an_event(tx, household):
    me = principal_for(household["wouter"])
    await arm(me)
    await Attachment(
        cove_id=household["shared"].id,
        object_key="attachments/lease",
        filename="lease.pdf",
        mime="application/pdf",
        byte_size=1,
        description="The signed lease.",
        status=AttachmentStatus.READY.value,
    ).save()
    events = await whats_new(me)
    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "file"
    assert event["cove"] == "household"
    assert event["key"] == "attachments/lease"
    assert event["filename"] == "lease.pdf"


async def test_tool_rejects_garbage_since(tx, household):
    me = principal_for(household["wouter"])
    result = await tool_whats_new(me, since="last tuesday")
    assert result["error"] == "invalid_since"


async def test_tool_wraps_events_and_echoes_since(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes.md", "A note.", message="x")
    moment = "2020-01-01T00:00:00"
    result = await tool_whats_new(me, since=moment)
    assert result["since"] == moment
    assert [e["path"] for e in result["events"]] == ["notes.md"]
