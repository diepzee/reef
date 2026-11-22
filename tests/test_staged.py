"""Staged tokens: a reset makes every token issued before it stale."""

import time
from datetime import timedelta

import pytest
from conftest import caller_for

from reef.config import get_settings
from reef.methods import MethodError, staged


@pytest.fixture(autouse=True)
def secret(monkeypatch) -> None:
    """Give staged tokens a signing secret long enough to use.

    :param monkeypatch: pytest's monkeypatch fixture
    """
    monkeypatch.setattr(get_settings().web, "session_secret", "s" * 40)


async def _reset_at(seed, person, offset: timedelta) -> None:
    """Flag a person as a test account with ``reset_at`` set, as the owner.

    :param seed: the connection that is not bound by row security
    :param person: the person to flag
    :param offset: when ``reset_at`` is, relative to now
    """
    await seed.execute(
        "INSERT INTO test_accounts (person_id, reset_at) VALUES ($1, now() + $2)",
        person.id,
        offset,
    )


async def test_token_staged_before_reset_is_stale(household, seed):
    caller = caller_for(household["wouter"])
    token = staged.stage(caller, "forget", {"engram": "x"}, now=time.time() - 60)[
        "token"
    ]
    await _reset_at(seed, household["wouter"], timedelta())
    with pytest.raises(MethodError) as err:
        await staged.take(caller, "forget", token)
    assert err.value.code == "stale_token"


async def test_token_staged_after_reset_works(household, seed):
    caller = caller_for(household["wouter"])
    await _reset_at(seed, household["wouter"], timedelta(hours=-1))
    token = staged.stage(caller, "forget", {"engram": "x"})["token"]
    assert await staged.take(caller, "forget", token) == {"engram": "x"}


async def test_unflagged_person_tokens_unaffected(household):
    caller = caller_for(household["wouter"])
    token = staged.stage(caller, "forget", {"engram": "x"})["token"]
    assert await staged.take(caller, "forget", token) == {"engram": "x"}
