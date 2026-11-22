"""The local test account: created, flagged, shared with nobody, and reset."""

import evalkit
import local_account
import pytest
from conftest import MemoryStore

from reef.access import Principal
from reef.db import transaction_scope
from reef.engrams import list_engrams
from reef.identity import person_by_email
from reef.testaccount.fixture import read_fixture

EMAIL = "conversations@reef.local"


async def test_creates_and_flags(seed):
    """A first run makes the person, flags them, and loads the made-up person."""
    person_id = await local_account.ensure_local_account(store=MemoryStore())
    assert await seed.fetchval(
        "SELECT count(*) FROM test_accounts WHERE person_id = $1", person_id
    )
    members = await seed.fetchval(
        "SELECT count(*) FROM memberships m JOIN spaces s ON s.id = m.space_id "
        "WHERE s.slug = 'household'"
    )
    assert members == 1
    identity = await person_by_email(EMAIL)
    principal = Principal(person_id=identity.person_id, email=identity.email)
    async with transaction_scope():
        engrams = await list_engrams(principal, "personal")
    assert len(engrams) >= len(read_fixture().engrams)


async def test_twice_is_safe(seed):
    """A second run returns the same id and leaves one copy of the fixture."""
    first = await local_account.ensure_local_account(store=MemoryStore())
    identity = await person_by_email(EMAIL)
    principal = Principal(person_id=identity.person_id, email=identity.email)
    async with transaction_scope():
        before = len(await list_engrams(principal, "personal"))
    second = await local_account.ensure_local_account(store=MemoryStore())
    async with transaction_scope():
        after = len(await list_engrams(principal, "personal"))
    assert first == second
    assert before == after
    assert await seed.fetchval("SELECT count(*) FROM test_accounts") == 1
    assert (
        await seed.fetchval("SELECT count(*) FROM spaces WHERE slug = 'household'") == 1
    )


async def test_refuses_remote(monkeypatch):
    """A Railway environment stops the run before anything is written."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(evalkit.NotLocal):
        await local_account.ensure_local_account(store=MemoryStore())
