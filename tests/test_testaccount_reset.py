"""Resetting a test account to the made-up person, and telling when it is ready."""

import asyncio
import logging
from uuid import UUID, uuid4

import pytest

from reef.access import Principal
from reef.attachments import S3ObjectStore, add_attachment
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import PERSONA_PATH, save_engram
from reef.methods import Caller, find
from reef.models import Person, Space
from reef.promotion import prepare_promotion
from reef.testaccount import (
    NotATestAccount,
    Readiness,
    SharedWithOthers,
    configured_store,
    readiness,
    reset,
)
from reef.testaccount.fixture import read_fixture
from reef.testaccount.lifecycle import _LOCK_CLASS, _start
from reef.usage import refresh_activation
from tests.conftest import MemoryStore


class RecordingStore(MemoryStore):
    """A memory store that notes every key it is asked to erase."""

    def __init__(self) -> None:
        """Start with nothing erased."""
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        """Note the key, then forget its bytes.

        :param key: object key
        """
        self.deleted.append(key)
        await super().delete(key)


class BrokenStore(MemoryStore):
    """A memory store whose erasing always fails."""

    async def delete(self, key: str) -> None:
        """Fail, naming the key the way a storage error might.

        :param key: object key
        :raises OSError: always
        """
        raise OSError(f"could not delete {key}")


@pytest.fixture(autouse=True)
def services_off(monkeypatch) -> None:
    """Start every test with no embedder and with file reading off.

    :param monkeypatch: pytest's monkeypatch fixture
    """
    settings = get_settings()
    monkeypatch.setattr(settings.embedding, "url", "")
    monkeypatch.setattr(settings.reading, "enabled", False)


def principal_for(person: Person) -> Principal:
    """Return the principal a signed-in person acts as.

    :param person: a seeded person
    :returns: the principal
    """
    return Principal(person_id=person.id, email=person.email)


async def _flag(seed, person: Person) -> None:
    """Flag a person as a test account, as the operator would.

    :param seed: the connection that is not bound by row security
    :param person: the person to flag
    """
    await seed.execute("INSERT INTO test_accounts (person_id) VALUES ($1)", person.id)


async def _test_person(
    graph, seed, email: str = "lode@example.test"
) -> tuple[Person, Space]:
    """Create a flagged person with a personal space and a shared space of their own.

    :param graph: the topology builders
    :param seed: the connection that is not bound by row security
    :param email: the person's address
    :returns: the person and their shared space
    """
    person = await graph.person(email, "Lode")
    await graph.personal_space(person)
    lab = await graph.shared_space("lab", person)
    await _flag(seed, person)
    return person, lab


async def _save(principal: Principal, space: str, path: str, body: str) -> None:
    """Save an engram in a transaction of its own, as a tool call would.

    :param principal: who saves it
    :param space: the person's name for the space
    :param path: the engram's path
    :param body: its text
    """
    async with transaction_scope():
        await save_engram(principal, space, path, body, message="x")


async def _store_file(principal: Principal, space: str, store: MemoryStore) -> None:
    """Store one small file, as a tool call would.

    :param principal: who stores it
    :param space: the person's name for the space
    :param store: where its bytes go
    """
    await add_attachment(
        principal,
        space,
        b"bytes",
        "text/plain",
        filename="note.txt",
        description="a note",
        store=store,
    )


async def _engram_count(seed, space_ids: list[UUID]) -> int:
    """Count the engrams in some spaces, past row security.

    :param seed: the connection that is not bound by row security
    :param space_ids: the spaces
    :returns: how many engrams they hold
    """
    return await seed.fetchval(
        "SELECT count(*) FROM engrams WHERE space_id = ANY($1::uuid[])",
        list(space_ids),
    )


async def test_reset_refuses_real_account(household, seed):
    """An unflagged account is refused before anything is touched."""
    me = principal_for(household["wouter"])
    await _save(me, "personal", "one.md", "one")
    await _save(me, "personal", "two.md", "two")
    store = RecordingStore()
    await _store_file(me, "personal", store)

    with pytest.raises(NotATestAccount):
        await reset(me, store)

    assert await _engram_count(seed, [household["w_personal"].id]) == 2
    assert store.deleted == []


async def test_refused_reset_does_not_read_the_fixture(household, monkeypatch):
    """The flag is checked before the made-up person is read from the package."""
    reads: list[bool] = []

    def recording_read() -> None:
        """Note the read; a refused reset must never get here."""
        reads.append(True)

    monkeypatch.setattr("reef.testaccount.lifecycle.read_fixture", recording_read)
    with pytest.raises(NotATestAccount):
        await reset(principal_for(household["wouter"]), None)
    assert reads == []


async def test_reset_refuses_shared_space(household, seed):
    """A flagged account that shares a space is refused, owner or member."""
    owner = principal_for(household["wouter"])
    member = principal_for(household["partner"])
    await _save(owner, "personal", "mine.md", "mine")
    await _save(owner, "household", "ours.md", "ours")
    await _save(member, "personal", "theirs.md", "theirs")
    spaces = [household[key].id for key in ("w_personal", "p_personal", "shared")]
    store = RecordingStore()
    await _store_file(owner, "household", store)

    for person in (household["wouter"], household["partner"]):
        await _flag(seed, person)
        with pytest.raises(SharedWithOthers):
            await reset(principal_for(person), store)
        await seed.execute("DELETE FROM test_accounts")

    assert await _engram_count(seed, spaces) == 3
    assert store.deleted == []
    assert await seed.fetchval("SELECT count(*) FROM attachments") == 1


async def test_reset_spares_a_space_joined_during_it(graph, seed, monkeypatch):
    """A space the account is admitted to after the check is not wiped."""
    person, _ = await _test_person(graph, seed)
    owner = await graph.person("owner@example.test", "Owner")
    await graph.personal_space(owner)
    theirs = await graph.shared_space("theirs", owner)
    await _save(principal_for(owner), "theirs", "keep.md", "keep me")

    async def start_then_admitted(principal: Principal) -> None:
        """Pass the check, then be admitted to the owner's space at once.

        :param principal: the person being reset
        """
        await _start(principal)
        await graph.add_membership(person, theirs, "member")

    monkeypatch.setattr("reef.testaccount.lifecycle._start", start_then_admitted)
    await reset(principal_for(person), RecordingStore())

    assert await _engram_count(seed, [theirs.id]) == 1


async def test_reset_restores_fixture(graph, seed):
    """Whatever the account did since, a reset leaves exactly the made-up person."""
    person, lab = await _test_person(graph, seed)
    me = principal_for(person)
    fixture = read_fixture()
    store = RecordingStore()
    await reset(me, store)
    loaded = await seed.fetch("SELECT object_key FROM attachments")

    changed = fixture.engrams[0]
    await _save(me, "personal", changed.path, "overwritten")
    await _save(me, "personal", "extra.md", "an extra engram")
    await _save(me, "lab", "notes.md", "a note in the shared space")
    async with transaction_scope():
        await prepare_promotion(me, "extra.md", "lab")
    assert await seed.fetchval("SELECT count(*) FROM promotions") == 1
    extra = await add_attachment(
        me,
        "personal",
        b"extra bytes",
        "text/plain",
        filename="extra.txt",
        description="an extra file",
        store=store,
    )
    pending_key = f"attachments/{uuid4().hex}"
    await seed.execute(
        "INSERT INTO attachments (id, space_id, object_key, filename, mime, "
        "byte_size, description, status) VALUES "
        "(gen_random_uuid(), $1, $2, 'half.pdf', 'application/pdf', 1, 'half', "
        "'pending')",
        lab.id,
        pending_key,
    )
    job = await seed.fetchval(
        "INSERT INTO jobs (id, person_id, kind, status) VALUES "
        "(gen_random_uuid(), $1, 'x', 'queued') RETURNING id",
        person.id,
    )
    removed = {row["object_key"] for row in loaded} | {extra.object_key, pending_key}
    store.deleted.clear()

    result = await reset(me, store)

    assert result.fixture_version == fixture.version
    assert (result.engrams, result.files) == (len(fixture.engrams), len(fixture.files))
    personal = await seed.fetch(
        "SELECT e.path, e.body, (SELECT count(*) FROM revisions r "
        "WHERE r.engram_id = e.id) AS revisions FROM engrams e "
        "JOIN spaces s ON s.id = e.space_id WHERE s.kind = 'personal'"
    )
    assert {row["path"]: row["body"] for row in personal} == {
        PERSONA_PATH: fixture.persona,
        **{engram.path: engram.body for engram in fixture.engrams},
    }
    assert {row["revisions"] for row in personal} == {1}
    assert await _engram_count(seed, [lab.id]) == 0
    files = await seed.fetch("SELECT object_key, status, space_id FROM attachments")
    assert sorted(row["status"] for row in files) == ["ready"] * len(fixture.files)
    assert not {row["object_key"] for row in files} & removed
    assert lab.id not in {row["space_id"] for row in files}
    assert set(store.deleted) == removed
    assert await seed.fetchval("SELECT count(*) FROM jobs WHERE id = $1", job) == 0
    assert await seed.fetchval("SELECT count(*) FROM promotions") == 0


async def test_reset_without_storage(graph, seed):
    """With no object storage, file rows go, no bytes are erased, none are loaded."""
    person, _ = await _test_person(graph, seed)
    me = principal_for(person)
    await add_attachment(
        me,
        "personal",
        b"bytes",
        "text/plain",
        filename="kept.txt",
        description="a file",
        store=MemoryStore(),
    )

    result = await reset(me, None)

    assert result.files == 0
    assert result.services["files"] is False
    assert await seed.fetchval("SELECT count(*) FROM attachments") == 0


async def test_reset_twice_concurrently(graph, seed):
    """Two resets at once both succeed, and the fixture is there exactly once."""
    person, _ = await _test_person(graph, seed)
    me = principal_for(person)

    await asyncio.gather(reset(me, RecordingStore()), reset(me, RecordingStore()))

    rows = await seed.fetch(
        "SELECT path, count(*) AS copies FROM engrams GROUP BY path"
    )
    assert len(rows) == len(read_fixture().engrams) + 1
    assert {row["copies"] for row in rows} == {1}
    assert await seed.fetchval("SELECT count(*) FROM attachments") == len(
        read_fixture().files
    )


async def test_reset_waits_for_the_person_lock(graph, seed):
    """A reset takes the person's lock before anything else, and waits for it."""
    person, _ = await _test_person(graph, seed)
    key = (_LOCK_CLASS, str(person.id))
    await seed.execute("SELECT pg_advisory_lock($1, hashtext($2))", *key)

    running = asyncio.create_task(reset(principal_for(person), RecordingStore()))
    waited = False
    for _ in range(100):
        waited = await seed.fetchval(
            "SELECT count(*) > 0 FROM pg_locks WHERE locktype = 'advisory' "
            "AND NOT granted"
        )
        if waited or running.done():
            break
        await asyncio.sleep(0.05)
    assert await seed.fetchval("SELECT reset_at FROM test_accounts") is None
    await seed.execute("SELECT pg_advisory_unlock($1, hashtext($2))", *key)
    await running

    assert waited
    assert await seed.fetchval("SELECT reset_at FROM test_accounts") is not None


async def test_reset_clears_usage(graph, seed):
    """Opening an engram leaves usage and activation; a reset removes both."""
    person, _ = await _test_person(graph, seed)
    me = principal_for(person)
    await reset(me, RecordingStore())
    caller = Caller(principal=me, writer="assistant", session_id=None)
    async with transaction_scope():
        [opened] = await find.open(
            caller, "", [f"personal/{read_fixture().engrams[0].path}"]
        )
        assert "error" not in opened
        await refresh_activation(me)
    counts = "SELECT count(*) FROM {} WHERE person_id = $1"
    assert await seed.fetchval(counts.format("engram_access"), person.id) == 1
    assert await seed.fetchval(counts.format("engram_heat"), person.id) == 1

    await reset(me, RecordingStore())

    assert await seed.fetchval(counts.format("engram_access"), person.id) == 0
    assert await seed.fetchval(counts.format("engram_heat"), person.id) == 0


async def test_erase_failure_logs_a_count_only(graph, seed, caplog):
    """Bytes that cannot be erased leave a count in the log, never a key or a person."""
    person, _ = await _test_person(graph, seed)
    me = principal_for(person)
    stored = await add_attachment(
        me,
        "personal",
        b"bytes",
        "text/plain",
        filename="gone.txt",
        description="a file",
        store=MemoryStore(),
    )

    with caplog.at_level(logging.DEBUG):
        await reset(me, BrokenStore())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "could not erase 1 of 1 stored files" in logged
    assert stored.object_key not in caplog.text
    assert str(person.id) not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


async def test_readiness_after_reset(graph, seed, monkeypatch):
    """Readiness is true at once with no services, and counts what is missing."""
    person, _ = await _test_person(graph, seed)
    me = principal_for(person)
    settings = get_settings()
    fixture = read_fixture()

    result = await reset(me, RecordingStore())

    assert result.services == {
        "embedding": False,
        "rerank": bool(settings.rerank.url and settings.rerank.enabled),
        "file_text": False,
        "files": True,
    }
    assert await readiness(me) == Readiness(
        ready=True, vectors_missing=0, files_unread=0, jobs_waiting=0
    )

    monkeypatch.setattr(settings.embedding, "url", "http://embedder.internal")
    monkeypatch.setattr(settings.reading, "enabled", True)
    result = await reset(me, RecordingStore())
    ready = await readiness(me)

    assert result.services["embedding"] and result.services["file_text"]
    # The persona is an engram too, and it gets a vector like the others.
    engrams = len(fixture.engrams) + 1
    assert ready == Readiness(
        ready=False,
        vectors_missing=engrams,
        files_unread=len(fixture.files),
        jobs_waiting=engrams + len(fixture.files),
    )
    # Embedding waits a while after a save; a reset does not.
    due = await seed.fetchval(
        "SELECT bool_and(run_after <= LOCALTIMESTAMP) FROM jobs WHERE person_id = $1",
        person.id,
    )
    assert due


def test_configured_store(monkeypatch):
    """Object storage counts as configured only with a bucket and its address."""
    files = get_settings().files
    monkeypatch.setattr(files, "bucket", "")
    monkeypatch.setattr(files, "endpoint", "")
    assert configured_store() is None
    monkeypatch.setattr(files, "bucket", "reef")
    assert configured_store() is None
    monkeypatch.setattr(files, "endpoint", "https://storage.example")
    assert isinstance(configured_store(), S3ObjectStore)
