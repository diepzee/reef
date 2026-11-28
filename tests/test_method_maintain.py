"""Maintain: ``correct``, ``undo`` and ``forget``.

Like the capture tests, these manage their own transactions: every method
opens its own, as it does behind the tools.
"""

import asyncio
from dataclasses import replace

import pytest
from asyncpg import LockNotAvailableError
from conftest import Store, attach_file, caller_for, save_as

from reef.access import arm
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.engrams import get_engram
from reef.methods import REGISTRY, Caller, MethodError, find, maintain
from reef.models import Engram, Revision

CAR = "The car is a blue estate.\n\nInsured with Ethias."


@pytest.fixture(autouse=True)
def secret(monkeypatch) -> None:
    """Give staged tokens a signing secret long enough to use.

    :param monkeypatch: pytest's monkeypatch fixture
    """
    monkeypatch.setattr(get_settings().web, "session_secret", "m" * 40)


@pytest.fixture
def store(monkeypatch) -> Store:
    """Stand in for the object store where files are opened and deleted.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the stand-in
    """
    fake = Store()
    fake.deleted = []

    async def delete(key: str) -> None:
        """Note the deleted key.

        :param key: the object key
        """
        fake.deleted.append(key)

    fake.delete = delete
    monkeypatch.setattr(maintain, "S3ObjectStore", lambda: fake)
    monkeypatch.setattr(find, "S3ObjectStore", lambda: fake)
    return fake


async def _read(caller: Caller, space: str, path: str) -> Engram | None:
    """Read an engram as it is committed now.

    :param caller: who reads it
    :param space: the caller's name for the space
    :param path: the engram's path
    :returns: the engram, or None
    """
    async with transaction_scope():
        return await get_engram(caller.principal, space, path)


async def _history(caller: Caller, space: str, path: str) -> list[Revision]:
    """List an engram's revisions, oldest first.

    :param caller: who reads them
    :param space: the caller's name for the space
    :param path: the engram's path
    :returns: the revisions
    """
    async with transaction_scope():
        engram = await get_engram(caller.principal, space, path)
        return (
            await Revision.objects()
            .where(Revision.engram_id == engram.id)
            .order_by(Revision.created_at)
        )


async def _versions(me: Caller, space: str = "household") -> None:
    """Write car.md three times, titled and tagged differently each time.

    :param me: who writes
    :param space: the space to write in
    """
    await save_as(me, space, "car.md", "one", title="First", tags=["a"])
    await save_as(me, space, "car.md", "two", title="Second", tags=["b"])
    await save_as(me, space, "car.md", "three", title="Third", tags=["c"])


def test_the_three_are_methods():
    """Each maintain method is registered under its name."""
    for name in ("correct", "undo", "forget"):
        assert name in REGISTRY


# --- correct ---------------------------------------------------------------------


async def test_correct_replaces_one_passage(household):
    """One passage changes; history keeps the old text and the change message."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", CAR)
    done = await maintain.correct(
        me, "fix colour", "household", "car.md", "blue", "green", "colour was wrong"
    )
    assert done == {"space": "household", "engram": "car.md", "version": 2}
    engram = await _read(me, "household", "car.md")
    assert engram.body == CAR.replace("blue", "green")
    first, second = await _history(me, "household", "car.md")
    assert first.body == CAR
    assert second.message == "colour was wrong"


@pytest.mark.parametrize("writer", ["assistant", "person", "system"])
async def test_correct_records_the_writer_on_the_revision(household, writer):
    """A correction stores the caller's writer kind on the new revision."""
    me = replace(caller_for(household["wouter"]), writer=writer)
    await save_as(me, "household", "car.md", CAR)
    await maintain.correct(me, "", "household", "car.md", "blue", "green", "fix")
    history = await _history(me, "household", "car.md")
    assert len(history) == 2
    assert history[-1].channel == writer


@pytest.mark.parametrize(
    ("old", "code"),
    [
        ("purple", "passage_not_found"),
        ("", "passage_not_found"),
        ("e", "passage_ambiguous"),
    ],
)
async def test_correct_needs_the_passage_exactly_once(household, old, code):
    """A passage that is missing or occurs more than once changes nothing."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", CAR)
    with pytest.raises(MethodError) as refused:
        await maintain.correct(me, "", "household", "car.md", old, "x", "m")
    assert refused.value.code == code
    assert (await _read(me, "household", "car.md")).version == 1


async def test_correct_refuses_a_stale_version_and_a_missing_engram(household):
    """An old expected version conflicts; a missing engram is not found."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", CAR)
    with pytest.raises(MethodError) as stale:
        await maintain.correct(
            me, "", "household", "car.md", "blue", "red", "m", expected_version=7
        )
    assert stale.value.code == "version_conflict"
    with pytest.raises(MethodError) as missing:
        await maintain.correct(me, "", "household", "nope.md", "a", "b", "m")
    assert missing.value.code == "not_found"


# --- the persona, spaces and viewers ---------------------------------------------


async def test_maintain_refuses_the_persona_before_anything_else(household):
    """The persona changes through instruct only, even in a space with none."""
    me = caller_for(household["wouter"])
    calls = [
        maintain.correct(me, "", "household", "meta/persona.md", "a", "b", "m"),
        maintain.undo(me, "", "household", "meta/persona.md"),
        maintain.forget(me, "", "household", engram="meta/persona.md"),
        maintain.forget(me, "", "nowhere", engram="META/Persona.md"),
    ]
    for call in calls:
        with pytest.raises(MethodError) as refused:
            await call
        assert refused.value.code == "use_instruct"


async def test_maintain_refuses_other_protected_paths(household):
    """Other meta/ paths keep today's refusal."""
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await maintain.forget(me, "", "household", engram="meta/protocol.md")
    assert refused.value.code == "protected_path"


async def test_maintain_refuses_unknown_spaces_and_viewers(household, graph, store):
    """A viewer is told the space is read only; a stranger's space is unknown."""
    person = await graph.person("viewer@example.test", "Viewer")
    await graph.personal_space(person, slug="viewer")
    await graph.add_membership(person, household["shared"], "viewer", alias="household")
    viewer = caller_for(person)
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", CAR)
    key = await attach_file(me, "household", "A policy.")
    for who, space, code in [
        (viewer, "household", "read_only"),
        (me, "nowhere", "unknown_space"),
    ]:
        calls = [
            maintain.correct(who, "", space, "car.md", "blue", "red", "m"),
            maintain.undo(who, "", space, "car.md"),
            maintain.forget(who, "", space, engram="car.md"),
            maintain.forget(who, "", space, file=key),
        ]
        for call in calls:
            with pytest.raises(MethodError) as refused:
                await call
            assert refused.value.code == code


# --- undo ------------------------------------------------------------------------


async def test_undo_restores_the_previous_version_as_a_new_change(household):
    """Body, title and tags come back as version 4; nothing is lost."""
    me = caller_for(household["wouter"])
    await _versions(me)
    done = await maintain.undo(me, "oops", "household", "car.md")
    assert done == {"space": "household", "engram": "car.md", "version": 4}
    engram = await _read(me, "household", "car.md")
    assert (engram.body, engram.title, list(engram.tags)) == ("two", "Second", ["b"])
    history = await _history(me, "household", "car.md")
    assert [r.body for r in history] == ["one", "two", "three", "two"]
    assert history[-1].message == "Undo to version 2"


@pytest.mark.parametrize("writer", ["assistant", "person", "system"])
async def test_undo_records_the_writer_on_the_revision(household, writer):
    """An undo stores the caller's writer kind on the restored revision."""
    me = replace(caller_for(household["wouter"]), writer=writer)
    await _versions(me)
    await maintain.undo(me, "", "household", "car.md")
    history = await _history(me, "household", "car.md")
    assert len(history) == 4
    assert history[-1].channel == writer


async def test_undo_to_a_given_version(household):
    """A named version is restored, however far back."""
    me = caller_for(household["wouter"])
    await _versions(me)
    await maintain.undo(me, "", "household", "car.md", version=1)
    engram = await _read(me, "household", "car.md")
    assert (engram.body, engram.title, list(engram.tags)) == ("one", "First", ["a"])
    assert engram.version == 4
    assert (await _history(me, "household", "car.md"))[
        -1
    ].message == "Undo to version 1"


async def test_undo_at_version_one_refuses(household):
    """The first version has nothing before it; neither has the current one."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", "one")
    for version in (None, 1):
        with pytest.raises(MethodError) as refused:
            await maintain.undo(me, "", "household", "car.md", version=version)
        assert refused.value.code == "nothing_to_undo"
    await save_as(me, "household", "car.md", "two")
    with pytest.raises(MethodError) as same:
        await maintain.undo(me, "", "household", "car.md", version=2)
    assert same.value.code == "nothing_to_undo"


@pytest.mark.parametrize("version", [0, -1, 3])
async def test_undo_refuses_a_version_that_does_not_exist(household, version):
    """A version below 1 or above the current one is not there."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", "one")
    await save_as(me, "household", "car.md", "two")
    with pytest.raises(MethodError) as refused:
        await maintain.undo(me, "", "household", "car.md", version=version)
    assert refused.value.code == "no_such_version"


async def test_undo_cannot_bring_back_a_forgotten_engram(household):
    """A forgotten engram has no history left to restore."""
    me = caller_for(household["wouter"])
    await _versions(me)
    staged = await maintain.forget(me, "", "household", engram="car.md")
    await maintain.forget(me, "", "household", engram="car.md", confirm=staged["token"])
    with pytest.raises(MethodError) as refused:
        await maintain.undo(me, "", "household", "car.md")
    assert refused.value.code == "not_found"


# --- forget ----------------------------------------------------------------------


async def test_forget_needs_the_token(household):
    """The first call deletes nothing; the second, with the token, does."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", CAR, title="The car")
    staged = await maintain.forget(me, "", "household", engram="car.md")
    assert staged["next"] == (
        "Show the person the effect and ask. After a yes, send this same call again "
        "with every argument unchanged, and add `confirm` with this token."
    )
    assert staged["expires_in"] == 600
    assert "The car" in staged["effect"]
    assert "car.md" in staged["effect"]
    assert "history" in staged["effect"]
    assert "every member" in staged["effect"].lower()
    assert "Ethias" not in staged["effect"]
    assert await _read(me, "household", "car.md") is not None
    done = await maintain.forget(
        me, "", "household", engram="car.md", confirm=staged["token"]
    )
    assert done == {"forgotten": "household/car.md"}
    assert await _read(me, "household", "car.md") is None


async def test_forget_token_cannot_delete_something_else(household):
    """A token for engram A is refused for engram B, which stays."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "a.md", "A")
    await save_as(me, "household", "b.md", "B")
    staged = await maintain.forget(me, "", "household", engram="a.md")
    for kwargs in ({"engram": "b.md"}, {"file": "a.md"}):
        with pytest.raises(MethodError) as refused:
            await maintain.forget(
                me, "", "household", confirm=staged["token"], **kwargs
            )
        assert refused.value.code == "stale_token"
    with pytest.raises(MethodError) as other_space:
        await maintain.forget(
            me, "", "personal", engram="a.md", confirm=staged["token"]
        )
    assert other_space.value.code == "stale_token"
    assert await _read(me, "household", "b.md") is not None
    assert await _read(me, "household", "a.md") is not None


async def test_forget_unreadable_is_not_found(household):
    """A missing engram and one in someone else's space look the same."""
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    await save_as(partner, "personal", "secret.md", "Mine.")
    answers = []
    for target in ("missing.md", "secret.md"):
        with pytest.raises(MethodError) as refused:
            await maintain.forget(me, "", "personal", engram=target)
        answers.append((refused.value.code, refused.value.message))
    assert answers[0] == answers[1]
    assert answers[0][0] == "not_found"
    with pytest.raises(MethodError) as file:
        await maintain.forget(me, "", "household", file="no/such/key")
    assert file.value.code == "not_found"


@pytest.mark.parametrize("kwargs", [{}, {"engram": "a.md", "file": "k"}])
async def test_forget_takes_exactly_one_target(household, kwargs):
    """Neither target, or both, is refused."""
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await maintain.forget(me, "", "household", **kwargs)
    assert refused.value.code == "one_target"


async def test_forget_replayed_token_after_deletion_is_not_found(household):
    """Using a token again once the engram is gone deletes nothing more."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", CAR)
    staged = await maintain.forget(me, "", "household", engram="car.md")
    await maintain.forget(me, "", "household", engram="car.md", confirm=staged["token"])
    await save_as(me, "household", "other.md", "Other.")
    with pytest.raises(MethodError) as replay:
        await maintain.forget(
            me, "", "household", engram="car.md", confirm=staged["token"]
        )
    assert replay.value.code == "not_found"
    assert await _read(me, "household", "other.md") is not None


async def test_forget_a_file(household, store):
    """A file is described by name and size, then deleted with its bytes."""
    me = caller_for(household["wouter"])
    key = await attach_file(me, "household", "A policy.")
    staged = await maintain.forget(me, "", "household", file=key)
    assert staged["next"] == (
        "Show the person the effect and ask. After a yes, send this same call again "
        "with every argument unchanged, and add `confirm` with this token."
    )
    assert "policy.pdf" in staged["effect"]
    assert "4 bytes" in staged["effect"]
    assert store.deleted == []
    done = await maintain.forget(me, "", "household", file=key, confirm=staged["token"])
    assert done == {"forgotten": f"household/file/{key}"}
    assert store.deleted == [key]
    with pytest.raises(MethodError) as replay:
        await maintain.forget(me, "", "household", file=key, confirm=staged["token"])
    assert replay.value.code == "not_found"


async def test_forget_file_checks_and_deletes_in_one_transaction(
    household, store, monkeypatch, seed
):
    """Check and delete together, committing before the object store is called."""
    me = caller_for(household["wouter"])
    key = await attach_file(me, "household", "A policy.")
    staged = await maintain.forget(me, "", "household", file=key)
    checks: list[int] = []
    deletes: list[int] = []
    run_querystring = DB.run_querystring

    async def track_transactions(engine, querystring, *args, **kwargs):
        """Record the transactions used for space checks and the row deletion.

        :param engine: the database engine
        :param querystring: the query to execute
        :param args: further positional arguments
        :param kwargs: further keyword arguments
        :returns: the database result
        """
        query, _ = querystring.compile_string(engine_type=engine.engine_type)
        if 'FROM "spaces"' in query or query.startswith('DELETE FROM "attachments"'):
            transaction = engine.current_transaction.get()
            assert transaction is not None
            identity = await transaction.connection.fetchval("SELECT txid_current()")
            (deletes if query.startswith("DELETE") else checks).append(identity)
        return await run_querystring(querystring, *args, **kwargs)

    async def delete_bytes(object_key: str) -> None:
        """Require the metadata deletion to be committed before deleting bytes.

        :param object_key: the file's storage key
        """
        assert object_key == key
        assert not await seed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM attachments WHERE object_key = $1)", key
        )
        store.deleted.append(key)

    monkeypatch.setattr(type(DB), "run_querystring", track_transactions)
    monkeypatch.setattr(store, "delete", delete_bytes)
    await maintain.forget(me, "", "household", file=key, confirm=staged["token"])
    assert checks and deletes
    assert len(set(checks + deletes)) == 1
    assert store.deleted == [key]


@pytest.mark.parametrize("with_space_id", [False, True])
async def test_forget_file_requires_the_staged_space(household, store, with_space_id):
    """A token missing the matching space identity never deletes a file."""
    me = caller_for(household["wouter"])
    key = await attach_file(me, "household", "A policy.")
    subject = {"space": "household", "file": key}
    if with_space_id:
        subject["space_id"] = str(household["w_personal"].id)
    staged = maintain.staged.stage(me, maintain.FORGET, subject)
    with pytest.raises(MethodError) as refused:
        await maintain.forget(me, "", "household", file=key, confirm=staged["token"])
    assert refused.value.code == "not_found"
    assert store.deleted == []
    assert (await find.open(me, "", [f"household/file/{key}"]))[0]["kind"] == "file"


async def test_forget_file_locks_the_row_before_deleting(
    household, store, monkeypatch, seed
):
    """A held confirming call locks its row and only one confirmation deletes bytes."""
    me = caller_for(household["wouter"])
    key = await attach_file(me, "household", "A policy.")
    staged = await maintain.forget(me, "", "household", file=key)
    selected = asyncio.Event()
    second_read = asyncio.Event()
    release = asyncio.Event()
    run_querystring = DB.run_querystring

    async def hold_after_read(engine, querystring, *args, **kwargs):
        """Hold the first confirming call after it has read the attachment.

        :param engine: the database engine
        :param querystring: the query to execute
        :param args: further positional arguments
        :param kwargs: further keyword arguments
        :returns: the database result
        """
        query, parameters = querystring.compile_string(engine_type=engine.engine_type)
        attachment_read = (
            query.startswith("SELECT")
            and 'FROM "attachments"' in query
            and key in parameters
        )
        if attachment_read and selected.is_set():
            second_read.set()
        result = await run_querystring(querystring, *args, **kwargs)
        if attachment_read and result and not selected.is_set():
            selected.set()
            await release.wait()
        return result

    monkeypatch.setattr(type(DB), "run_querystring", hold_after_read)
    first = asyncio.create_task(
        maintain.forget(me, "", "household", file=key, confirm=staged["token"])
    )
    await selected.wait()
    try:
        with pytest.raises(LockNotAvailableError):
            async with seed.transaction():
                await seed.fetchrow(
                    "SELECT id FROM attachments WHERE object_key = $1 FOR UPDATE NOWAIT",
                    key,
                )
        second = asyncio.create_task(
            maintain.forget(me, "", "household", file=key, confirm=staged["token"])
        )
        await second_read.wait()
    finally:
        release.set()
        await first
    with pytest.raises(MethodError) as refused:
        await second
    assert refused.value.code == "not_found"
    assert store.deleted == [key]


async def test_forget_in_a_personal_space_does_not_name_other_members(household):
    """In a personal space nobody else loses anything."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "note.md", "Note.")
    staged = await maintain.forget(me, "", "personal", engram="note.md")
    assert "member" not in staged["effect"].lower()


async def test_undo_keeps_the_log_tag_a_migration_added(household):
    """A log tagged without a revision is still a log after undoing an entry."""
    from reef.methods import capture

    me = caller_for(household["wouter"])
    await save_as(me, "household", "trip-log.md", "# Trip\n")
    async with transaction_scope():
        await arm(me.principal)
        await Engram.update({Engram.tags: ["log"]}).where(Engram.path == "trip-log.md")
    await capture.log(me, "", "household", "trip-log.md", "Day 1", "Walked.")
    await maintain.undo(me, "", "household", "trip-log.md")
    engram = await _read(me, "household", "trip-log.md")
    assert "log" in engram.tags
    assert "Day 1" not in engram.body
    await capture.log(me, "", "household", "trip-log.md", "Day 2", "Rested.")
    assert "Day 2" in (await _read(me, "household", "trip-log.md")).body


async def test_forget_token_does_not_delete_a_recreated_engram(household):
    """A replayed token cannot delete a new engram at the old path."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "car.md", CAR)
    staged = await maintain.forget(me, "", "household", engram="car.md")
    await maintain.forget(me, "", "household", engram="car.md", confirm=staged["token"])
    await save_as(me, "household", "car.md", "A new car.")
    with pytest.raises(MethodError) as replay:
        await maintain.forget(
            me, "", "household", engram="car.md", confirm=staged["token"]
        )
    assert replay.value.code == "not_found"
    assert (await _read(me, "household", "car.md")).body == "A new car."


async def test_forget_token_is_refused_when_the_name_points_elsewhere(household, graph):
    """After a rename, a token staged for the old space deletes nothing."""
    from reef.spaces import rename_space

    me = caller_for(household["wouter"])
    club = await graph.shared_space("club", household["wouter"])
    await save_as(me, "household", "car.md", CAR)
    await save_as(me, "club", "car.md", "Club car.")
    staged = await maintain.forget(me, "", "household", engram="car.md")
    async with transaction_scope():
        await rename_space(me.principal, "household", "home")
        await rename_space(me.principal, "club", "household")
    with pytest.raises(MethodError) as refused:
        await maintain.forget(
            me, "", "household", engram="car.md", confirm=staged["token"]
        )
    assert refused.value.code == "not_found"
    assert (await _read(me, "household", "car.md")).body == "Club car."
    assert club.id


@pytest.mark.parametrize("target", ["engram", "file"])
@pytest.mark.parametrize("loss", ["viewer", "removed"])
async def test_forget_rechecks_permission_at_confirm(
    household, graph, store, target, loss
):
    """Losing write access after staging preserves the target and its history."""
    person = household["partner"]
    me = caller_for(person)
    owner = caller_for(household["wouter"])
    saved = await save_as(me, "household", "car.md", CAR)
    key = await attach_file(me, "household", "Car insurance.")
    args = {target: "car.md" if target == "engram" else key}
    first = await maintain.forget(me, "", "household", **args)
    if loss == "viewer":
        await graph.set_role(person, household["shared"], "viewer")
    else:
        await graph.drop_membership(person, household["shared"])

    with pytest.raises(MethodError) as refused:
        await maintain.forget(me, "", "household", confirm=first["token"], **args)

    assert refused.value.code == ("read_only" if loss == "viewer" else "unknown_space")
    current = await _read(owner, "household", "car.md")
    assert (current.id, current.body, current.version) == (saved.id, CAR, 1)
    assert len(await _history(owner, "household", "car.md")) == 1
    [file] = await find.open(owner, "", [f"household/file/{key}"])
    assert "error" not in file
    assert store.deleted == []
