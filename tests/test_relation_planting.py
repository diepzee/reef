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
    assert caught.value.code == "relation_block_in_body"


async def test_correct_cannot_plant_a_block(household) -> None:
    """A replacement passage cannot introduce a leading relation block."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "new.md", "Original.")
    with pytest.raises(MethodError) as caught:
        await maintain.correct(
            me, "", "personal", "new.md", "Original.", BLOCK + "new", "fix"
        )
    assert caught.value.code == "relation_block_in_body"


async def test_shared_section_cannot_plant_a_block(household, monkeypatch) -> None:
    """Moving an interior block to the start of a shared body is refused."""
    from reef.config import get_settings

    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "notes.md", "Keep.\n\n" + BLOCK + "section")
    with pytest.raises(MethodError) as caught:
        await share.share(
            me, "", "notes.md", "household", section=BLOCK + "section", to_path="new.md"
        )
    assert caught.value.code == "relation_block_in_body"
    assert "rewrite" in caught.value.message.lower()
    assert "write" in caught.value.message


@pytest.mark.parametrize("section", [None, "Safe section."])
async def test_share_refuses_a_legacy_block_before_staging(
    household, seed, section
) -> None:
    """A legacy source needs a rewrite before either kind of share can proceed."""
    me = caller_for(household["wouter"])
    source = await save_as(me, "personal", "notes.md", "Original.")
    await seed.execute(
        "UPDATE engrams SET body = $1 WHERE id = $2", BLOCK + "Safe section.", source.id
    )
    with pytest.raises(MethodError) as caught:
        await share.share(
            me, "", "notes.md", "household", section=section, to_path="shared.md"
        )
    assert caught.value.code == "relation_block_in_body"
    assert "rewrite" in caught.value.message.lower()
    assert await seed.fetchval("SELECT count(*) FROM promotions") == 0


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


async def test_write_checks_size_before_reading_block(household, monkeypatch) -> None:
    """Removing a large block cannot bypass the input size limit."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    monkeypatch.setattr(get_settings().engrams, "max_chars", 80)
    body = "---\nabout: [[old.md]]" + " " * 100 + "\n---\nnew"
    with pytest.raises(MethodError) as caught:
        await capture.write(
            me, "", "personal", [{"path": "new.md", "body": body}], "save"
        )
    assert caught.value.code == "engram_too_large"


async def test_web_checks_size_before_reading_block(api, world, monkeypatch) -> None:
    """The web editor applies the same raw body size limit."""
    _login(api, world[0])
    headers = {"X-Rif-Csrf": "1"}
    await api.put(
        "/api/engrams/team/old.md",
        json={"body": "old", "message": "save"},
        headers=headers,
    )
    monkeypatch.setattr(get_settings().engrams, "max_chars", 80)
    body = "---\nabout: [[old.md]]" + " " * 100 + "\n---\nnew"
    response = await api.put(
        "/api/engrams/team/new.md",
        json={"body": body, "message": "save"},
        headers=headers,
    )
    assert response.status_code == 400


async def test_save_counts_repeated_targets_once(household) -> None:
    """Explicit callers have the same distinct-target limit as block writers."""
    from reef.models import LinkKind
    from reef.relations import Relations

    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    await save_as(
        me,
        "personal",
        "new.md",
        "new",
        relations=Relations({LinkKind.ABOUT: ("old.md",) * 21}),
    )


async def test_repeated_relation_key_is_refused(household) -> None:
    """A second occurrence cannot silently replace the first list."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    body = "---\nabout: [[old.md]]\nabout:\n---\nnew"
    with pytest.raises(MethodError) as caught:
        await capture.write(
            me, "", "personal", [{"path": "new.md", "body": body}], "save"
        )
    assert caught.value.code == "relation_invalid"


async def test_bom_is_kept_when_write_reads_a_block(household, seed) -> None:
    """A leading byte-order mark survives removal of the relation block."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    body = "\ufeff---\nabout: [[old.md]]\n---\nnew"
    await capture.write(me, "", "personal", [{"path": "new.md", "body": body}], "save")
    async with transaction_scope():
        assert (
            await get_engram(me.principal, "personal", "new.md")
        ).body == "\ufeffnew"
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE source = 'author'") == 1
    )


@pytest.mark.parametrize(
    "separator", ["\r", "\u2028", "\u2029", "\x85", "\f", "\v", "\x1c", "\x1d", "\x1e"]
)
@pytest.mark.parametrize("operation", ["correct", "save"])
async def test_editor_line_normalization_cannot_activate_a_block(
    household, separator, operation
) -> None:
    """A block hidden by other line separators cannot be planted."""
    from reef.relations import InvalidRelations

    me = caller_for(household["wouter"])
    await save_as(me, "personal", "new.md", "Original.")
    body = separator.join(("---", "replaces: [[old.md]]", "---", "body"))
    with pytest.raises((MethodError, InvalidRelations)) as caught:
        if operation == "correct":
            await maintain.correct(
                me, "", "personal", "new.md", "Original.", body, "fix"
            )
        else:
            await save_as(me, "personal", "new.md", body)
    assert caught.value.code == "relation_block_in_body"
    async with transaction_scope():
        assert (
            await get_engram(me.principal, "personal", "new.md")
        ).body == "Original."


@pytest.mark.parametrize("prefix", ["\n", " ", "\ufeff"])
@pytest.mark.parametrize("operation", ["correct", "save"])
async def test_trimming_a_prefix_cannot_activate_a_block(
    household, prefix, operation
) -> None:
    """Whitespace and a byte-order mark cannot hide a leading relation block."""
    from reef.relations import InvalidRelations

    me = caller_for(household["wouter"])
    await save_as(me, "personal", "new.md", "Original.")
    with pytest.raises((MethodError, InvalidRelations)) as caught:
        if operation == "correct":
            await maintain.correct(
                me, "", "personal", "new.md", "Original.", prefix + BLOCK, "fix"
            )
        else:
            await save_as(me, "personal", "new.md", prefix + BLOCK)
    assert caught.value.code == "relation_block_in_body"


async def test_refused_plant_cannot_become_a_link_on_web_save(api, world, seed) -> None:
    """A refused edit leaves only the original text for the person's later save."""
    me = caller_for(world[0])
    _login(api, world[0])
    await save_as(me, "team", "old.md", "old")
    await save_as(me, "team", "new.md", "Original.")
    with pytest.raises(MethodError):
        await maintain.correct(
            me,
            "",
            "team",
            "new.md",
            "Original.",
            "\n---\nreplaces: [[old.md]]\n---\nnew",
            "fix",
        )
    original = (await api.get("/api/engrams/team/new.md")).json()["body"]
    assert original == "Original."
    response = await api.put(
        "/api/engrams/team/new.md",
        json={"body": original, "message": "save"},
        headers={"X-Rif-Csrf": "1"},
    )
    assert response.status_code == 200
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE kind = 'supersedes'") == 0
    )


@pytest.mark.parametrize(
    "separator", ["\u2028", "\u2029", "\x85", "\f", "\v", "\x1c", "\x1d", "\x1e"]
)
async def test_non_lf_inside_an_ordinary_line_stays_inert(
    api, world, seed, separator
) -> None:
    """The original review example stays plain text under either parser view."""
    me = caller_for(world[0])
    _login(api, world[0])
    await save_as(me, "team", "old.md", "old")
    await save_as(me, "team", "new.md", "Original.")
    body = f"---\n---{separator}replaces: [[old.md]]\n---\nbody"
    await maintain.correct(me, "", "team", "new.md", "Original.", body, "fix")
    response = await api.put(
        "/api/engrams/team/new.md",
        json={"body": body, "message": "save"},
        headers={"X-Rif-Csrf": "1"},
    )
    assert response.status_code == 200
    assert response.json()["body"] == body
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE kind = 'supersedes'") == 0
    )


@pytest.mark.parametrize("caller", ["write", "save"])
async def test_twenty_one_links_are_refused_by_a_save(household, caller) -> None:
    """Both parsed and explicit relation lists enforce the twenty-link limit."""
    from reef.models import LinkKind
    from reef.relations import InvalidRelations, Relations

    me = caller_for(household["wouter"])
    paths = tuple(f"target-{number}.md" for number in range(21))
    for path in paths:
        await save_as(me, "personal", path, "target")
    if caller == "write":
        body = (
            "---\nabout: " + ", ".join(f"[[{path}]]" for path in paths) + "\n---\nnew"
        )
        with pytest.raises(MethodError) as caught:
            await capture.write(
                me, "", "personal", [{"path": "new.md", "body": body}], "save"
            )
    else:
        with pytest.raises(InvalidRelations) as caught:
            await save_as(
                me,
                "personal",
                "new.md",
                "new",
                relations=Relations({LinkKind.ABOUT: paths}),
            )
    assert caught.value.code == "relation_too_many"
    async with transaction_scope():
        assert await get_engram(me.principal, "personal", "new.md") is None
