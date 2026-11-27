"""Capture: ``remember``, ``log``, ``write`` and ``store_file``.

Like the find tests, these manage their own transactions: every method
opens its own, as it does behind the tools, and has to see committed rows.
"""

import asyncio
import base64

import pytest
from conftest import Store, caller_for, save_as

from reef import sections
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import get_engram
from reef.methods import REGISTRY, Caller, MethodError, capture, find, logs
from reef.models import Engram, Revision

#: A small cap, so a test can fill a part with a few entries.
CAP = 1_000


async def test_replaces_stages_the_whole_batch(household, monkeypatch, seed) -> None:
    """Three engrams wait for yes, then all bodies and relations are saved."""
    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    await save_as(me, "personal", "unchanged.md", "before")
    items = [
        {"path": "unchanged.md", "body": "after"},
        {"path": "new.md", "body": "---\nreplaces: [[old.md]]\n---\nnew"},
        {"path": "third.md", "body": "third"},
    ]
    staged = await capture.write(me, "", "personal", items, "replace")
    assert set(staged) == {"effect", "token", "expires_in"}
    assert (
        "personal/old.md" in staged["effect"] and "personal/new.md" in staged["effect"]
    )
    assert (await _read(me, "personal", "unchanged.md")).body == "before"
    assert await _read(me, "personal", "new.md") is None
    assert await _read(me, "personal", "third.md") is None
    result = await capture.write(
        me, "", "personal", items, "replace", confirm=staged["token"]
    )
    assert len(result) == 3
    assert (await _read(me, "personal", "new.md")).body == "new"
    assert (await _read(me, "personal", "unchanged.md")).body == "after"
    assert (await _read(me, "personal", "third.md")).body == "third"
    assert (
        await seed.fetchval("SELECT kind FROM links WHERE source = 'author'")
        == "supersedes"
    )
    assert isinstance(await capture.write(me, "", "personal", items, "again"), list)


async def test_confirmed_write_retry_and_removed_link_replay(
    household, monkeypatch
) -> None:
    """A retry writes normally, but a removed replacement needs a fresh yes."""
    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    items = [{"path": "new.md", "body": "---\nreplaces: [[old.md]]\n---\nnew"}]
    first = await capture.write(me, "", "personal", items, "replace")
    await capture.write(me, "", "personal", items, "replace", confirm=first["token"])
    retry = await capture.write(
        me, "", "personal", items, "retry", confirm=first["token"]
    )
    assert isinstance(retry, list)
    await capture.write(
        me,
        "",
        "personal",
        [{"path": "new.md", "body": "---\nreplaces:\n---\nnew"}],
        "remove",
    )
    with pytest.raises(MethodError) as caught:
        await capture.write(me, "", "personal", items, "replay", confirm=first["token"])
    assert caught.value.code == "stale_token"


async def test_confirmation_flag_means_a_token_was_taken_for_pairs(
    household, monkeypatch
) -> None:
    """Arbitrary confirm text and a no-op retry confer no confirmation authority."""
    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    real_save = capture.save_engram
    flags: list[bool] = []

    async def recording_save(*args, **kwargs):
        """Record whether storage receives actual confirmation authority."""
        flags.append(kwargs["replacements_confirmed"])
        return await real_save(*args, **kwargs)

    monkeypatch.setattr(capture, "save_engram", recording_save)
    await capture.write(
        me,
        "",
        "personal",
        [{"path": "plain.md", "body": "plain"}],
        "save",
        confirm="arbitrary text",
    )
    items = [{"path": "new.md", "body": "---\nreplaces: [[old.md]]\n---\nnew"}]
    first = await capture.write(me, "", "personal", items, "replace")
    await capture.write(me, "", "personal", items, "replace", confirm=first["token"])
    await capture.write(me, "", "personal", items, "retry", confirm=first["token"])
    assert flags == [False, True, False]


@pytest.mark.parametrize(
    "case, code",
    [
        ("path", "invalid_path"),
        ("meta", "protected_path"),
        ("size", "engram_too_large"),
        ("version", "version_conflict"),
        ("private", "private_content"),
    ],
)
async def test_first_write_checks_the_save_before_asking(
    household, monkeypatch, case, code
) -> None:
    """A refusal already knowable on the first call names the affected path."""
    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    space = "household" if case == "private" else "personal"
    await save_as(me, space, "old.md", "old")
    path = {"path": "../new.md", "meta": "meta/note.md"}.get(case, "new.md")
    text = "new"
    if case == "size":
        monkeypatch.setattr(get_settings().engrams, "max_chars", 80)
        text = "x" * 100
    if case == "private":
        text = " ".join(f"secretword{number}" for number in range(50))
        await save_as(me, "personal", "private.md", text)
    item = {"path": path, "body": "---\nreplaces: [[old.md]]\n---\n" + text}
    if case == "version":
        await save_as(me, space, path, "before")
        item["expected_version"] = 99
    with pytest.raises(MethodError) as caught:
        await capture.write(me, "", space, [item], "replace")
    assert caught.value.code == code
    assert path in caught.value.message


@pytest.mark.parametrize("change", ["source_version", "target_id"])
async def test_confirmation_binds_source_version_and_target_id(
    household, monkeypatch, seed, change
) -> None:
    """The same paths cannot conceal an edited source or a recreated target."""
    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    old = await save_as(me, "personal", "old.md", "old")
    await save_as(me, "personal", "new.md", "before")
    items = [{"path": "new.md", "body": "---\nreplaces: [[old.md]]\n---\nnew"}]
    first = await capture.write(me, "", "personal", items, "replace")
    if change == "source_version":
        await save_as(me, "personal", "new.md", "changed")
    else:
        await seed.execute("DELETE FROM engrams WHERE id = $1", old.id)
        await save_as(me, "personal", "old.md", "another engram")
    with pytest.raises(MethodError) as caught:
        await capture.write(
            me, "", "personal", items, "replace", confirm=first["token"]
        )
    assert caught.value.code == "stale_token"


@pytest.mark.parametrize("mismatch", ["pairs", "source", "person", "action"])
async def test_replaces_token_is_bound_to_this_call(
    household, monkeypatch, mismatch
) -> None:
    """Another pair, source, person or action cannot reuse the token."""
    from reef.methods import staged

    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)
    me = caller_for(household["wouter"])
    for path in ("old.md", "other.md"):
        await save_as(me, "household", path, path)
    items = [{"path": "new.md", "body": "---\nreplaces: [[old.md]]\n---\nnew"}]
    result = await capture.write(me, "", "household", items, "replace")
    token = result["token"]
    if mismatch == "pairs":
        items[0]["body"] = items[0]["body"].replace("old.md", "other.md")
    elif mismatch == "source":
        items[0]["path"] = "elsewhere.md"
    elif mismatch == "person":
        me = caller_for(household["partner"])
    else:
        token = staged.stage(me, "forget", {})["token"]
    with pytest.raises(MethodError) as caught:
        await capture.write(me, "", "household", items, "replace", confirm=token)
    assert caught.value.code == "stale_token"
    assert await _read(me, "household", items[0]["path"]) is None


@pytest.mark.parametrize("key", ["about", "contradicts", "part_of", "replaces"])
async def test_other_relations_and_removal_do_not_stage(household, key) -> None:
    """Only adding replaces asks for confirmation."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    value = "" if key == "replaces" else "[[old.md]]"
    result = await capture.write(
        me,
        "",
        "personal",
        [{"path": "new.md", "body": f"---\n{key}: {value}\n---\nnew"}],
        "link",
    )
    assert isinstance(result, list)


@pytest.mark.parametrize("operation", ["correct", "remember", "log", "undo"])
async def test_passage_changes_do_not_read_relation_blocks(
    household, seed, operation
) -> None:
    """Legacy relation blocks cannot be stored again by passage tools."""
    from reef.methods import maintain

    me = caller_for(household["wouter"])
    block = "---\nreplaces: [[old.md]]\n---\n"
    await save_as(me, "personal", "old.md", "old")
    path = "inbox.md" if operation == "remember" else "notes.md"
    body = block + "# Notes\n\nOriginal."
    existing = await save_as(
        me,
        "personal",
        path,
        "# Notes\n\nOriginal.",
        tags=["log"] if operation == "log" else [],
    )
    await seed.execute("UPDATE engrams SET body = $1 WHERE id = $2", body, existing.id)
    await seed.execute(
        "UPDATE revisions SET body = $1 WHERE engram_id = $2", body, existing.id
    )
    if operation == "undo":
        await save_as(me, "personal", path, "later")
    with pytest.raises(MethodError) as caught:
        if operation == "correct":
            await maintain.correct(
                me, "", "personal", path, "Original.", "Changed.", "fix"
            )
        elif operation == "remember":
            await capture.remember(me, "", "New fact.")
        elif operation == "log":
            await capture.log(me, "", "personal", path, "Today", "New entry.")
        else:
            await maintain.undo(me, "", "personal", path)
    assert caught.value.code == "relation_block_in_body"
    assert "rewrite" in caught.value.message.lower()
    assert "write" in caught.value.message
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE kind = 'supersedes'") == 0
    )


async def _read(caller: Caller, space: str, path: str) -> Engram | None:
    """Read an engram as it is committed now.

    :param caller: who reads it
    :param space: the caller's name for the space
    :param path: the engram's path
    :returns: the engram, or None
    """
    async with transaction_scope():
        return await get_engram(caller.principal, space, path)


async def _channels(caller: Caller, space: str, path: str) -> list[str | None]:
    """List who made each change to an engram, oldest first.

    :param caller: who reads the history
    :param space: the caller's name for the space
    :param path: the engram's path
    :returns: the writer kind of each revision
    """
    async with transaction_scope():
        engram = await get_engram(caller.principal, space, path)
        rows = (
            await Revision.select(Revision.channel)
            .where(Revision.engram_id == engram.id)
            .order_by(Revision.created_at)
        )
    return [row["channel"] for row in rows]


@pytest.fixture
def store(monkeypatch) -> Store:
    """Stand in for the object store where files are stored and opened.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the stand-in
    """
    fake = Store()
    monkeypatch.setattr(capture, "S3ObjectStore", lambda: fake)
    monkeypatch.setattr(find, "S3ObjectStore", lambda: fake)
    return fake


@pytest.fixture
def small_cap(monkeypatch) -> int:
    """Lower the engram size cap, so a log fills up in a few entries.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the cap
    """
    monkeypatch.setattr(get_settings().engrams, "max_chars", CAP)
    return CAP


def test_the_four_are_methods():
    """Each capture method is registered under its name."""
    for name in ("remember", "log", "write", "store_file"):
        assert name in REGISTRY


# --- remember --------------------------------------------------------------------


async def test_remember_adds_the_fact_to_the_inbox(household):
    """A fact lands in the personal inbox, once, even when sent twice."""
    me = caller_for(household["wouter"])
    first = await capture.remember(me, "a fact to keep", "the boiler is a Vaillant")
    again = await capture.remember(me, "a fact to keep", "the boiler is a Vaillant")
    assert first["space"] == "personal"
    assert first["engram"] == "inbox.md"
    assert first["duplicate"] is False
    assert first["appended"].endswith(" the boiler is a Vaillant")
    assert again == {"space": "personal", "engram": "inbox.md", "duplicate": True}
    inbox = await _read(me, "personal", "inbox.md")
    assert inbox.body.count("Vaillant") == 1


async def test_remember_keeps_a_fact_an_earlier_entry_contains(household):
    """The duplicate check is per entry, not a substring of the whole inbox.

    A substring test discarded genuinely new facts whenever a longer entry
    happened to contain their words -- reporting success and storing
    nothing, which is the worst failure a memory product has -- and let
    anyone sharing a space suppress its inbox by padding it with likely
    phrasings.
    """
    me = caller_for(household["wouter"])
    await capture.remember(me, "", "Wouter is allergic to penicillin and nuts")
    second = await capture.remember(me, "", "allergic to penicillin")
    assert second["duplicate"] is False
    inbox = await _read(me, "personal", "inbox.md")
    assert inbox.body.count("allergic to penicillin") == 2


async def test_remember_into_a_named_space(household):
    """A fact for a group goes to that space's inbox, and only there."""
    me = caller_for(household["wouter"])
    result = await capture.remember(
        me, "", "school run swaps to me on Fridays", "household"
    )
    assert result["space"] == "household"
    assert "Fridays" in (await _read(me, "household", "inbox.md")).body
    assert await _read(me, "personal", "inbox.md") is None


@pytest.mark.parametrize("writer", ["assistant", "person", "system"])
async def test_capture_records_who_wrote(household, writer):
    """The writer kind of the caller is kept on each change."""
    person = Caller(
        principal=caller_for(household["wouter"]).principal,
        writer=writer,
        session_id=None,
    )
    await capture.remember(person, "", "bin day is Tuesday")
    await capture.log(person, "", "personal", "climbing-log.md", "Day 1", "Hung.")
    await capture.write(
        person, "", "personal", [{"path": "plan.md", "body": "Hang twice."}], "create"
    )
    for path in ("inbox.md", "climbing-log.md", "plan.md"):
        assert await _channels(person, "personal", path) == [writer]


@pytest.mark.parametrize("writer", ["assistant", "person", "system"])
async def test_log_keeps_writer_on_appends_and_the_next_part(
    household, small_cap, writer
):
    """Appending, closing a full part and starting its successor keep the writer."""
    caller = Caller(
        principal=caller_for(household["wouter"]).principal,
        writer=writer,
        session_id=None,
    )
    await capture.log(caller, "", "personal", "training-log.md", "Day 1", "a" * 300)
    appended = await capture.log(
        caller, "", "personal", "training-log.md", "Day 2", "b" * 300
    )
    assert appended == {"engram": "training-log.md", "part": 1, "version": 2}
    assert await _channels(caller, "personal", "training-log.md") == [writer, writer]

    continued = await capture.log(
        caller, "", "personal", "training-log.md", "Day 3", "c" * 400
    )

    assert continued == {"engram": "training-log-2.md", "part": 2, "version": 1}
    assert await _channels(caller, "personal", "training-log.md") == [writer] * 3
    assert await _channels(caller, "personal", "training-log-2.md") == [writer]


# --- log -------------------------------------------------------------------------


async def test_log_appends_and_touches_nothing_else(household):
    """The entry lands at the end; everything before it is byte for byte the same."""
    me = caller_for(household["wouter"])
    before = "# Climbing log\n\nIntro  \n\n### 2026-01-01\n\nHung twice.\n\n\n"
    saved = await save_as(
        me, "personal", "climbing-log.md", before, title="Climbing", tags=["log", "x"]
    )
    result = await capture.log(
        me, "log the session", "personal", "climbing-log.md", "2026-01-02", "Sent it."
    )
    assert result == {"engram": "climbing-log.md", "part": 1, "version": 2}
    after = await _read(me, "personal", "climbing-log.md")
    assert after.body == before + "\n\n### 2026-01-02\n\nSent it.\n"
    assert after.title == "Climbing"
    assert after.tags == ["log", "x"]
    assert after.id == saved.id


async def test_log_creates_a_tagged_log(household):
    """A log that does not exist yet is created, tagged, at the normalized path."""
    me = caller_for(household["wouter"])
    result = await capture.log(
        me, "", "household", "Climbing Log", "2026-01-01", "First session."
    )
    assert result == {"engram": "climbing-log.md", "part": 1, "version": 1}
    created = await _read(me, "household", "climbing-log.md")
    assert created.tags == ["log"]
    assert created.body == "### 2026-01-01\n\nFirst session.\n"
    again = await capture.log(me, "", "household", "Climbing Log", "2026-01-02", "x")
    assert again == {"engram": "climbing-log.md", "part": 1, "version": 2}


@pytest.mark.parametrize("level", [2, 3])
@pytest.mark.parametrize("extra", [0, 1, 100])
async def test_log_caps_headings_so_entries_can_be_read(household, level, extra):
    """The whole heading line fits the parser's limit at either entry level."""
    me = caller_for(household["wouter"])
    marks = "#" * level
    await save_as(
        me, "personal", "climbing-log.md", f"{marks} First\n\nClimbed.", tags=["log"]
    )
    limit = sections.HEADING_CHARS
    heading = "é" * (limit - len(marks) - 1 + extra)
    await capture.log(me, "", "personal", "climbing-log.md", heading, "New entry.")
    parts = await logs.series(me, "personal", "climbing-log.md")
    [last] = logs.last_entries(parts, 1)
    assert last.endswith("New entry.")
    assert last.splitlines()[0] == f"{marks} {heading}"[:limit]


@pytest.mark.parametrize("level", [2, 3])
async def test_log_preserves_every_word_of_a_long_heading(household, level):
    """Overflow starts the entry text without splitting or losing words."""
    me = caller_for(household["wouter"])
    marks = "#" * level
    await save_as(me, "personal", "trip.md", f"{marks} First\n\nWalked.", tags=["log"])
    words = [f"destination{number}" for number in range(50)]
    await capture.log(me, "", "personal", "trip.md", " ".join(words), "Arrived.")
    parts = await logs.series(me, "personal", "trip.md")
    [last] = logs.last_entries(parts, 1)
    line, overflow, entry = last.split("\n\n")
    assert len(line) <= sections.HEADING_CHARS
    assert line.removeprefix(f"{marks} ").split() + overflow.split() == words
    assert entry == "Arrived."
    assert all(word in parts[0].body.split() for word in words)


async def test_log_keeps_the_level_of_a_log_with_level_two_entries(household):
    """A log of ``##`` entries gets a ``##`` entry, and brief still reads them all.

    A ``###`` inside fenced code is code, not an entry, so it does not count.
    """
    me = caller_for(household["wouter"])
    before = (
        "# Climbing log\n\n## Day 1\n\n```\n### not an entry\n```\n\n## Day 2\n\nx\n"
    )
    await save_as(me, "personal", "climbing-log.md", before, tags=["log"])
    await capture.log(me, "", "personal", "climbing-log.md", "Day 3", "Sent it.")
    after = await _read(me, "personal", "climbing-log.md")
    assert after.body == before + "\n\n## Day 3\n\nSent it.\n"
    result = await find.brief(me, "", "climbing", entries=3)
    [log] = [item for item in result["engrams"] if item["engram"] == "climbing-log.md"]
    assert [entry.splitlines()[0] for entry in log["entries"]] == [
        "## Day 1",
        "## Day 2",
        "## Day 3",
    ]


async def test_log_keeps_level_two_in_a_new_part(household, small_cap):
    """A full log of ``##`` entries goes on in part two with a ``##`` entry."""
    me = caller_for(household["wouter"])
    before = "## Day 1\n\n" + "x" * 800 + "\n"
    await save_as(me, "personal", "climbing-log.md", before, tags=["log"])
    result = await capture.log(
        me, "", "personal", "climbing-log.md", "Day 2", "y" * 300
    )
    assert result["part"] == 2
    two = await _read(me, "personal", "climbing-log-2.md")
    assert "\n\n## Day 2\n\n" in two.body
    assert "###" not in two.body


async def test_log_keeps_level_three_when_the_log_has_any(household):
    """A log with a ``###`` entry anywhere gets ``###`` entries."""
    me = caller_for(household["wouter"])
    before = "# Log\n\n## 2026\n\n### Day 1\n\nx\n"
    await save_as(me, "personal", "climbing-log.md", before, tags=["log"])
    await capture.log(me, "", "personal", "climbing-log.md", "Day 2", "y")
    after = await _read(me, "personal", "climbing-log.md")
    assert after.body == before + "\n\n### Day 2\n\ny\n"


async def test_log_refuses_what_is_not_a_log(household):
    """An engram without the ``log`` tag is not turned into one."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "notes.md", "Plain notes.")
    with pytest.raises(MethodError) as refused:
        await capture.log(me, "", "personal", "notes.md", "Day 1", "x")
    assert refused.value.code == "not_a_log"
    assert refused.value.message != "not_a_log"
    assert (await _read(me, "personal", "notes.md")).body == "Plain notes."


async def test_log_starts_part_two_at_the_cap(household, small_cap):
    """A full log goes on in a tagged part two; the two link to each other."""
    me = caller_for(household["wouter"])
    entry = "x" * 300
    first = await capture.log(me, "", "personal", "climbing-log.md", "Day 1", entry)
    await capture.log(me, "", "personal", "climbing-log.md", "Day 2", entry)
    await capture.log(me, "", "personal", "climbing-log.md", "Day 3", entry)
    full = (await _read(me, "personal", "climbing-log.md")).body
    assert first["part"] == 1

    result = await capture.log(me, "", "personal", "climbing-log.md", "Day 4", entry)

    assert result == {"engram": "climbing-log-2.md", "part": 2, "version": 1}
    one = await _read(me, "personal", "climbing-log.md")
    two = await _read(me, "personal", "climbing-log-2.md")
    assert one.body == full + "\n\nContinued in [[climbing-log-2.md]].\n"
    assert len(one.body) <= small_cap
    assert two.tags == ["log"]
    assert two.title == "climbing-log (part 2)"
    assert two.body == f"Continued from [[climbing-log.md]].\n\n### Day 4\n\n{entry}\n"
    parts = await logs.series(me, "personal", "climbing-log.md")
    assert [part.path for part in parts] == ["climbing-log.md", "climbing-log-2.md"]
    [opened_one, opened_two] = await find.open(
        me, "", ["personal/climbing-log.md", "personal/climbing-log-2.md"]
    )
    assert [link["path"] for link in opened_one["links"]] == ["climbing-log-2.md"]
    assert [link["path"] for link in opened_two["links"]] == ["climbing-log.md"]

    # Naming part one still writes to the last part.
    later = await capture.log(me, "", "personal", "climbing-log.md", "Day 5", "y")
    assert later == {"engram": "climbing-log-2.md", "part": 2, "version": 2}


async def test_log_refuses_an_entry_bigger_than_the_cap(household, small_cap):
    """An entry that cannot fit even in a part of its own writes nothing."""
    me = caller_for(household["wouter"])
    await capture.log(me, "", "personal", "climbing-log.md", "Day 1", "Short.")
    before = await _read(me, "personal", "climbing-log.md")
    with pytest.raises(MethodError) as refused:
        await capture.log(
            me, "", "personal", "climbing-log.md", "Day 2", "x" * small_cap
        )
    assert refused.value.code == "entry_too_long"
    after = await _read(me, "personal", "climbing-log.md")
    assert (after.body, after.version) == (before.body, before.version)
    assert await _read(me, "personal", "climbing-log-2.md") is None
    with pytest.raises(MethodError) as refused:
        await capture.log(me, "", "personal", "new-log.md", "Day 1", "x" * small_cap)
    assert refused.value.code == "entry_too_long"
    assert await _read(me, "personal", "new-log.md") is None


async def test_log_naming_a_part_not_there_yet_adds_to_its_log(household):
    """``<stem>-<n>.md`` that does not exist names the log of ``<stem>.md``."""
    me = caller_for(household["wouter"])
    await capture.log(me, "", "personal", "climbing-log.md", "Day 1", "x")
    result = await capture.log(me, "", "personal", "climbing-log-2.md", "Day 2", "y")
    assert result == {"engram": "climbing-log.md", "part": 1, "version": 2}
    assert await _read(me, "personal", "climbing-log-2.md") is None


async def test_log_skips_a_part_name_already_taken(household, small_cap):
    """An engram that is not part of the log keeps its name and its text."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "climbing-log-2.md", "Something else.")
    await capture.log(me, "", "personal", "climbing-log.md", "Day 1", "x" * 600)
    result = await capture.log(
        me, "", "personal", "climbing-log.md", "Day 2", "y" * 400
    )
    assert result == {"engram": "climbing-log-3.md", "part": 3, "version": 1}
    assert (await _read(me, "personal", "climbing-log-2.md")).body == (
        "Something else."
    )
    one = await _read(me, "personal", "climbing-log.md")
    assert one.body.endswith("\n\nContinued in [[climbing-log-3.md]].\n")


async def test_log_leaves_out_a_closing_link_that_does_not_fit(household, small_cap):
    """A part filled by other means is not taken past the cap to close it."""
    me = caller_for(household["wouter"])
    full = "x" * (small_cap - 5)
    await save_as(me, "personal", "climbing-log.md", full, tags=["log"])
    result = await capture.log(me, "", "personal", "climbing-log.md", "Day 1", "y")
    assert result["engram"] == "climbing-log-2.md"
    assert (await _read(me, "personal", "climbing-log.md")).body == full


async def test_log_with_an_old_style_path_refuses_to_start_part_two(
    household, small_cap, seed
):
    """A log stored before paths were normalized keeps taking entries that fit.

    It cannot go on in a second part: that part would be stored under the
    normalized name, where the log's other parts are not looked for. So the
    entry that would start it is refused, and nothing is written.
    """
    me = caller_for(household["wouter"])
    first = "### Day 1\n\n" + "x" * 600 + "\n"
    await save_as(me, "personal", "notes/climbing-log.md", first, tags=["log"])
    # As it was stored before normalize_path existed.
    await seed.execute(
        "UPDATE engrams SET path = 'Notes/climbing-log.md' "
        "WHERE path = 'notes/climbing-log.md'"
    )
    fits = await capture.log(me, "", "personal", "Notes/climbing-log.md", "Day 2", "y")
    assert fits == {"engram": "Notes/climbing-log.md", "part": 1, "version": 2}
    before = await _read(me, "personal", "Notes/climbing-log.md")

    with pytest.raises(MethodError) as refused:
        await capture.log(
            me, "", "personal", "Notes/climbing-log.md", "Day 3", "z" * 400
        )

    assert refused.value.code == "log_needs_rename"
    assert refused.value.message != "log_needs_rename"
    after = await _read(me, "personal", "Notes/climbing-log.md")
    assert (after.body, after.version) == (before.body, before.version)
    assert "Continued in" not in after.body
    for orphan in ("notes/climbing-log-2.md", "Notes/climbing-log-2.md"):
        assert await _read(me, "personal", orphan) is None


@pytest.mark.parametrize("exists", [True, False])
async def test_two_logs_at_once_both_land(household, exists):
    """Calls to one log at the same time, each in its own transaction, all land."""
    me = caller_for(household["wouter"])
    before = "# Log\n\nIntro.\n" if exists else ""
    if exists:
        await save_as(me, "personal", "climbing-log.md", before, tags=["log"])
    headings = [f"Session {number}" for number in range(4)]
    await asyncio.gather(
        *(
            capture.log(me, "", "personal", "climbing-log.md", heading, "Climbed.")
            for heading in headings
        )
    )
    after = await _read(me, "personal", "climbing-log.md")
    assert after.body.startswith(before)
    for heading in headings:
        assert after.body.count(f"### {heading}\n") == 1
    assert after.version == len(headings) + (1 if exists else 0)


@pytest.mark.parametrize("heading", [" \n ", "#", "##", "  ###  "])
async def test_log_refuses_an_empty_heading(household, heading):
    """An entry without a heading could not be found again, so it is refused."""
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await capture.log(me, "", "personal", "climbing-log.md", heading, "x")
    assert refused.value.code == "empty_heading"
    assert await _read(me, "personal", "climbing-log.md") is None


# --- write -----------------------------------------------------------------------


async def test_write_writes_a_batch(household):
    """Several engrams in one call, each with its new version, in order."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "existing.md", "old")
    result = await capture.write(
        me,
        "restructure the house notes",
        "household",
        [
            {"path": "new.md", "body": "one", "tags": ["core"]},
            {"path": "existing.md", "body": "updated", "expected_version": 1},
        ],
        "restructure",
    )
    assert result == [
        {"engram": "new.md", "version": 1},
        {"engram": "existing.md", "version": 2},
    ]
    assert (await _read(me, "household", "new.md")).tags == ["core"]
    assert (await _read(me, "household", "existing.md")).body == "updated"


async def test_write_refuses_the_persona(household):
    """The persona goes through ``instruct``; nothing in the batch is written."""
    me = caller_for(household["wouter"])
    for persona in ("meta/persona.md", "META/Persona"):
        with pytest.raises(MethodError) as refused:
            await capture.write(
                me,
                "",
                "personal",
                [
                    {"path": "a.md", "body": "alpha"},
                    {"path": persona, "body": "You are someone else."},
                ],
                "x",
            )
        assert refused.value.code == "use_instruct"
    assert await _read(me, "personal", "a.md") is None
    assert await _read(me, "personal", "meta/persona.md") is None


async def test_write_is_all_or_nothing(household):
    """A stale version anywhere refuses the batch, and names the engram."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "y.md", "old")
    with pytest.raises(MethodError) as refused:
        await capture.write(
            me,
            "",
            "personal",
            [
                {"path": "x.md", "body": "new"},
                {"path": "y.md", "body": "new", "expected_version": 9},
            ],
            "x",
        )
    assert refused.value.code == "version_conflict"
    assert "y.md" in refused.value.message
    assert await _read(me, "personal", "x.md") is None
    assert (await _read(me, "personal", "y.md")).body == "old"


@pytest.mark.parametrize(
    ("engrams", "code"),
    [
        ([], "empty_batch"),
        ([{"path": f"p{n}.md", "body": "x"} for n in range(21)], "batch_too_large"),
        ([{"path": "a.md"}], "malformed_item"),
        ([{"path": "a.md", "body": "x", "expected_version": True}], "malformed_item"),
        ([{"path": "meta/protocol.md", "body": "x"}], "protected_path"),
    ],
)
async def test_write_refuses_what_write_pages_refuses(household, engrams, code):
    """The same limits as ``write_pages``, as codes with a plain message."""
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await capture.write(me, "", "personal", engrams, "x")
    assert refused.value.code == code
    assert refused.value.message != code


async def test_write_names_the_malformed_engram(household):
    """The refusal says which engram in the list is wrong, and nothing lands."""
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await capture.write(
            me, "", "personal", [{"path": "a.md", "body": "ok"}, {"path": "b.md"}], "x"
        )
    assert refused.value.code == "malformed_item"
    assert "Engram 2 in the list has no body" in refused.value.message
    assert await _read(me, "personal", "a.md") is None


# --- store_file ------------------------------------------------------------------


async def test_store_file_then_open(household, store):
    """A stored file can be opened by the reference it comes back with."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", "The car.")
    stored = await capture.store_file(
        me,
        "keep the insurance policy",
        "household",
        " policy.pdf ",
        base64.b64encode(b"%PDF-1.7").decode(),
        "application/pdf",
        "Car insurance policy, Ethias, 2026.",
        engram="car.md",
    )
    assert stored["ref"] == f"household/file/{stored['key']}"
    assert stored["filename"] == "policy.pdf"
    assert stored["size"] == 8
    [opened] = await find.open(me, "", [stored["ref"]])
    assert opened["kind"] == "file"
    assert opened["filename"] == "policy.pdf"
    assert opened["description"] == "Car insurance policy, Ethias, 2026."
    assert opened["mime"] == "application/pdf"


@pytest.mark.parametrize(
    ("filename", "data", "mime", "description", "code"),
    [
        ("a.pdf", "not base64!", "application/pdf", "A file.", "invalid_base64"),
        ("  ", "eA==", "application/pdf", "A file.", "invalid_filename"),
        ("a.pdf", "eA==", "pdf", "A file.", "invalid_mime"),
        # Stored, sent to the object store and signed into a URL: a header
        # smuggled in after a line break must not get through.
        (
            "a.pdf",
            "eA==",
            "text/html; charset=utf-8\r\nX-Evil: 1",
            "A file.",
            "invalid_mime",
        ),
        ("a.pdf", "eA==", "text/", "A file.", "invalid_mime"),
        ("a.pdf", "eA==", "application/pdf", "  ", "description_required"),
    ],
)
async def test_store_file_refuses_what_add_file_refuses(
    household, store, filename, data, mime, description, code
):
    """The same checks as ``add_file``, as codes with a plain message."""
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await capture.store_file(me, "", "personal", filename, data, mime, description)
    assert refused.value.code == code
    assert refused.value.message != code


async def test_store_file_refuses_a_file_over_the_limit(household, store, monkeypatch):
    """A file larger than Reef keeps is refused before it is stored."""
    monkeypatch.setattr(get_settings().files, "max_bytes", 4)
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await capture.store_file(
            me, "", "personal", "a.bin", "eHh4eHg=", "application/octet-stream", "x"
        )
    assert refused.value.code == "file_too_large"


# --- every method ----------------------------------------------------------------


async def test_capture_refuses_a_read_only_member(household, graph, store):
    """A viewer is told the space is read only, not that it does not exist."""
    person = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_space(person, slug="viewer")
    await graph.add_membership(person, household["shared"], "viewer", alias="household")
    viewer = caller_for(person)
    calls = [
        capture.remember(viewer, "", "a fact", "household"),
        capture.log(viewer, "", "household", "climbing-log.md", "Day 1", "x"),
        capture.write(viewer, "", "household", [{"path": "a.md", "body": "x"}], "x"),
        capture.store_file(
            viewer, "", "household", "a.pdf", "eA==", "application/pdf", "A file."
        ),
    ]
    for call in calls:
        with pytest.raises(MethodError) as refused:
            await call
        assert refused.value.code == "read_only"


async def test_capture_refuses_an_unknown_space(household, store):
    """Each method that takes a space says so plainly when there is none."""
    me = caller_for(household["wouter"])
    calls = [
        capture.remember(me, "", "a fact", "nowhere"),
        capture.log(me, "", "nowhere", "climbing-log.md", "Day 1", "x"),
        capture.write(me, "", "nowhere", [{"path": "a.md", "body": "x"}], "x"),
        capture.store_file(
            me, "", "nowhere", "a.pdf", "eA==", "application/pdf", "A file."
        ),
    ]
    for call in calls:
        with pytest.raises(MethodError) as refused:
            await call
        assert refused.value.code == "unknown_space"
        assert "nowhere" not in refused.value.message
