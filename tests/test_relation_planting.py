"""Text cannot plant a relation for a person's later editor save."""

import pytest
from conftest import _login, caller_for, save_as

from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import get_engram
from reef.methods import MethodError, capture, maintain, share

BLOCK = "---\nabout: [[old.md]]\n---\n"
ORDINARY = "---\nabout: quarterly review\n---\nNotes."


async def test_write_refuses_two_leading_relation_blocks(
    household, monkeypatch
) -> None:
    """Taking the first block cannot leave another one for a later save."""
    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    with pytest.raises(MethodError) as caught:
        await capture.write(
            me,
            "",
            "personal",
            [
                {
                    "path": "new.md",
                    "body": BLOCK.replace("about:", "replaces:") + BLOCK + "new",
                }
            ],
            "save",
        )
    assert caught.value.code == "relation_invalid"


async def test_correct_cannot_plant_a_block(household) -> None:
    """A replacement passage cannot introduce a leading relation block."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "new.md", "Original.")
    with pytest.raises(MethodError) as caught:
        await maintain.correct(
            me, "", "personal", "new.md", "Original.", BLOCK + "new", "fix"
        )
    assert caught.value.code == "relation_invalid"


async def test_shared_section_cannot_plant_a_block(household, monkeypatch) -> None:
    """Moving an interior block to the start of a shared body is refused."""
    from reef.config import get_settings

    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "notes.md", "Keep.\n\n" + BLOCK + "section")
    first = await share.share(
        me, "", "notes.md", "household", section=BLOCK + "section", to_path="new.md"
    )
    with pytest.raises(MethodError) as caught:
        await share.share(
            me,
            "",
            "notes.md",
            "household",
            section=BLOCK + "section",
            to_path="new.md",
            confirm=first["token"],
        )
    assert caught.value.code == "relation_invalid"


@pytest.mark.parametrize("operation", ["write", "correct"])
async def test_ordinary_key_text_stays_unchanged(household, operation) -> None:
    """A familiar key with prose as its value is ordinary Markdown."""
    me = caller_for(household["wouter"])
    if operation == "write":
        await capture.write(
            me, "", "personal", [{"path": "new.md", "body": ORDINARY}], "save"
        )
    else:
        await save_as(me, "personal", "new.md", "Original.")
        await maintain.correct(
            me, "", "personal", "new.md", "Original.", ORDINARY, "fix"
        )
    async with transaction_scope():
        assert (await get_engram(me.principal, "personal", "new.md")).body == ORDINARY


async def test_web_preserves_ordinary_key_text(api, world) -> None:
    """The editor does not interpret a relation key holding ordinary prose."""
    _login(api, world[0])
    response = await api.put(
        "/api/engrams/team/new.md",
        json={"body": ORDINARY, "message": "save"},
        headers={"X-Rif-Csrf": "1"},
    )
    assert response.status_code == 200
    assert response.json()["body"] == ORDINARY
