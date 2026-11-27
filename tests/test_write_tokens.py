"""A yes for a replacement authorizes one committed write."""

import asyncio
from hashlib import sha256

import asyncpg
import pytest
from conftest import caller_for, save_as

from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.engrams import delete_engram, get_engram
from reef.methods import MethodError, capture, staged
from reef.models import UsedWriteToken
from reef.write_tokens import consume


@pytest.fixture(autouse=True)
def signing_key(monkeypatch) -> None:
    """Give staged tokens a signing key for each test."""
    monkeypatch.setattr(get_settings().web, "session_secret", "x" * 32)


def replacement(body: str = "new") -> dict:
    """Build a replacement at the path the token will name.

    :param body: the new engram's text
    :returns: one write item
    """
    return {"path": "new.md", "body": "---\nreplaces: [[old.md]]\n---\n" + body}


@pytest.mark.parametrize("rollback", [False, True])
async def test_consume_waits_for_the_first_transaction(
    household, seed, rollback
) -> None:
    """Two connections resolve the same token only after the first transaction ends."""
    me = caller_for(household["wouter"])
    token = staged.stage(me, "write", {"test": True})["token"]
    await staged.take(me, "write", token)
    consumed = asyncio.Event()
    release = asyncio.Event()
    second_started = asyncio.Event()
    pids: dict[str, int] = {}

    async def first() -> None:
        """Consume and hold the first transaction until the test releases it."""
        async with transaction_scope():
            pids["first"] = (
                await UsedWriteToken.raw("SELECT pg_backend_pid() AS pid")
            )[0]["pid"]
            await consume(me, token)
            consumed.set()
            await release.wait()
            if rollback:
                await DB.current_transaction.get().rollback()

    async def second() -> str:
        """Try the same digest on a separate connection and report its outcome."""
        await consumed.wait()
        try:
            async with transaction_scope():
                pids["second"] = (
                    await UsedWriteToken.raw("SELECT pg_backend_pid() AS pid")
                )[0]["pid"]
                second_started.set()
                await consume(me, token)
        except MethodError as refused:
            return refused.code
        return "consumed"

    a = asyncio.create_task(first())
    b = asyncio.create_task(second())
    try:
        async with asyncio.timeout(10):
            await second_started.wait()
            assert pids["first"] != pids["second"]
            # Observe PostgreSQL's lock wait, rather than assuming a delay proves it.
            while pids["first"] not in await seed.fetchval(
                "SELECT pg_blocking_pids($1)", pids["second"]
            ):
                assert not b.done(), "the second consume did not wait"
                await asyncio.sleep(0.01)
            assert not b.done()
    finally:
        release.set()
        results = await asyncio.gather(a, b)
    assert results[1] == ("consumed" if rollback else "stale_token")
    assert await seed.fetchval("SELECT count(*) FROM used_write_tokens") == 1


@pytest.mark.parametrize("padded", [False, True])
@pytest.mark.parametrize("body", ["new", "a different engram at the same path"])
async def test_deleted_source_cannot_reuse_a_yes(household, seed, body, padded) -> None:
    """Deleting the source cannot make its used token valid again."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    staged = await capture.write(me, "", "personal", [replacement()], "save")
    assert (
        await seed.fetchval("SELECT count(*) FROM engrams WHERE path = 'new.md'") == 0
    )
    await capture.write(me, "", "personal", [replacement()], "save", staged["token"])
    async with transaction_scope():
        await delete_engram(me.principal, "personal", "new.md")
    token = staged["token"]
    if padded:
        payload, _, signature = token.rpartition(".")
        token = payload + "====." + signature
    with pytest.raises(MethodError) as caught:
        await capture.write(me, "", "personal", [replacement(body)], "save", token)
    assert caught.value.code == "stale_token"
    assert (
        await seed.fetchval("SELECT count(*) FROM engrams WHERE path = 'new.md'") == 0
    )
    assert (
        await seed.fetchval("SELECT count(*) FROM links WHERE kind = 'supersedes'") == 0
    )


async def test_failure_after_take_rolls_back_the_yes(
    household, seed, monkeypatch
) -> None:
    """A failure after one save rolls back both that save and token use."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    other = await save_as(me, "personal", "other.md", "original")
    batch = [
        replacement(),
        {"path": "other.md", "body": "changed", "expected_version": other.version},
    ]
    staged = await capture.write(me, "", "personal", batch, "save")
    save = capture._save

    async def fail_second(*args, **kwargs):
        """Force a version conflict after the first engram has been saved."""
        if args[2] == "other.md":
            assert await get_engram(me.principal, "personal", "new.md") is not None
            assert await UsedWriteToken.count() == 1
            assert (
                await seed.fetchval(
                    "SELECT count(*) FROM engrams WHERE path = 'new.md'"
                )
                == 0
            )
            raise MethodError("version_conflict")
        return await save(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(capture, "_save", fail_second)
        with pytest.raises(MethodError) as caught:
            await capture.write(me, "", "personal", batch, "save", staged["token"])
    assert caught.value.code == "version_conflict"
    assert await seed.fetchval("SELECT count(*) FROM used_write_tokens") == 0
    async with transaction_scope():
        assert await get_engram(me.principal, "personal", "new.md") is None
        assert (
            await get_engram(me.principal, "personal", "other.md")
        ).body == "original"
    assert await capture.write(me, "", "personal", batch, "save", staged["token"])


async def test_successful_retry_without_new_replacement_is_normal(household) -> None:
    """An unchanged replacement needs no second yes and writes normally."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "old.md", "old")
    staged = await capture.write(me, "", "personal", [replacement()], "save")
    await capture.write(me, "", "personal", [replacement()], "save", staged["token"])
    result = await capture.write(
        me, "", "personal", [replacement("updated")], "save", staged["token"]
    )
    assert result == [{"engram": "new.md", "version": 2}]


async def test_another_person_cannot_consume_the_yes(household) -> None:
    """A rejected caller leaves the token usable by its owner."""
    me = caller_for(household["wouter"])
    other = caller_for(household["partner"])
    await save_as(me, "household", "old.md", "old")
    staged = await capture.write(me, "", "household", [replacement()], "save")
    with pytest.raises(MethodError) as caught:
        await capture.write(
            other, "", "household", [replacement()], "save", staged["token"]
        )
    assert caught.value.code == "stale_token"
    assert await capture.write(
        me, "", "household", [replacement()], "save", staged["token"]
    )


async def test_token_rows_are_private_and_cascade(household, probe, seed) -> None:
    """Used digests stay private, immutable and tied to the account."""
    me = caller_for(household["wouter"])
    other = household["partner"]
    await save_as(me, "personal", "old.md", "old")
    staged = await capture.write(me, "", "personal", [replacement()], "save")
    await capture.write(me, "", "personal", [replacement()], "save", staged["token"])
    digest = sha256(staged["token"].encode()).hexdigest()
    async with probe.transaction():
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)", str(other.id)
        )
        assert await probe.fetch("SELECT * FROM used_write_tokens") == []
        # A digest for another person must not reveal a uniqueness conflict.
        await probe.execute(
            "INSERT INTO used_write_tokens (id, person_id, token_digest) VALUES (gen_random_uuid(), $1, $2)",
            other.id,
            digest,
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with probe.transaction():
                await probe.execute(
                    "INSERT INTO used_write_tokens (id, person_id, token_digest) VALUES (gen_random_uuid(), $1, $2)",
                    me.principal.person_id,
                    "a" * 64,
                )
        await probe.execute(
            "SELECT set_config('app.person_id', $1, true)", str(me.principal.person_id)
        )
        rows = await probe.fetch("SELECT * FROM used_write_tokens")
        assert len(rows) == 1 and rows[0]["token_digest"] == digest
        assert staged["token"] not in str(rows)
        for sql in (
            "DELETE FROM used_write_tokens",
            "UPDATE used_write_tokens SET token_digest = 'changed'",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                async with probe.transaction():
                    await probe.execute(sql)
    await seed.execute("DELETE FROM persons WHERE id = $1", other.id)
    assert (
        await seed.fetchval(
            "SELECT count(*) FROM used_write_tokens WHERE person_id = $1", other.id
        )
        == 0
    )
