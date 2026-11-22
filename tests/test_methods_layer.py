"""The method layer: every call logged, the reason kept private, staged tokens."""

import logging
from typing import get_args
from uuid import uuid4

import pytest

from reef import methods
from reef.access import Principal, arm
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.methods import Caller, MethodError, Writer, method, staged
from reef.models import CallLog
from reef.rls import WRITER_KINDS
from reef.sealing import seal
from reef.web import session

SECRET = "s" * 40


class Boom(Exception):
    """A failure a method could meet."""


@pytest.fixture
def logging_on(monkeypatch):
    """Switch call logging on, as the operator would."""
    monkeypatch.setattr(get_settings().calls, "log", True)


@pytest.fixture
def registry(monkeypatch) -> dict:
    """Give each test an empty registry, so test methods never leak out."""
    fresh: dict = {}
    monkeypatch.setattr(methods, "REGISTRY", fresh)
    return fresh


@pytest.fixture
def secret(monkeypatch):
    """Give the reef a signing secret long enough to stage with."""
    monkeypatch.setattr(get_settings().web, "session_secret", SECRET)


def caller_for(person) -> Caller:
    """Build the caller the MCP transport would build for this person."""
    principal = Principal(person_id=person.id, email=person.email)
    return Caller(principal=principal, writer="assistant", session_id="s1")


def somebody() -> Caller:
    """Build a caller for a person the database need not know."""
    principal = Principal(person_id=uuid4(), email="somebody@example.test")
    return Caller(principal=principal, writer="assistant", session_id=None)


async def _rows(caller: Caller) -> list[dict]:
    """Return the caller's call log, oldest first, in a transaction of its own."""
    async with transaction_scope():
        await arm(caller.principal)
        return await CallLog.select().order_by(CallLog.called_at)


async def test_reason_is_cut_to_200(household, logging_on, registry):
    seen: list[str] = []

    @method("probe")
    async def probe(caller: Caller, reason: str) -> list:
        seen.append(reason)
        return []

    me = caller_for(household["wouter"])
    await probe(me, "because " * 100)
    [row] = await _rows(me)
    assert len(seen[0]) == 200
    assert row["reason"] == seen[0]


async def test_empty_reason_is_accepted_and_counted(household, logging_on, registry):
    @method("probe")
    async def probe(caller: Caller, reason: str, *, n: int) -> list:
        return list(range(n))

    me = caller_for(household["wouter"])
    assert await probe(me, "", n=3) == [0, 1, 2]
    [row] = await _rows(me)
    assert row["reason"] == ""
    assert (row["method"], row["outcome"], row["returned"]) == ("probe", "ok", 3)
    assert (row["writer"], row["session_id"]) == ("assistant", "s1")


async def test_failed_method_is_logged_and_reraised(household, logging_on, registry):
    error = Boom("the engram is gone")

    @method("probe")
    async def probe(caller: Caller, reason: str) -> dict:
        # A method's own transaction rolls back; its call log row must not.
        async with transaction_scope():
            await arm(caller.principal)
            raise error

    me = caller_for(household["wouter"])
    with pytest.raises(Boom) as raised:
        await probe(me, "to find the boiler manual")
    assert raised.value is error
    [row] = await _rows(me)
    assert (row["method"], row["outcome"], row["returned"]) == ("probe", "Boom", 0)
    assert row["reason"] == "to find the boiler manual"


async def test_a_refusal_is_logged_by_its_code(household, logging_on, registry):
    @method("probe")
    async def probe(caller: Caller, reason: str) -> dict:
        raise MethodError("stale_token")

    me = caller_for(household["wouter"])
    with pytest.raises(MethodError):
        await probe(me, "")
    [row] = await _rows(me)
    assert (row["outcome"], row["returned"]) == ("stale_token", 0)


async def test_the_method_runs_outside_any_transaction(household, registry):
    # Methods open their own short transactions, so model calls can happen
    # outside them; the wrapper must not hold one open around the method.
    seen: list[object] = []

    @method("probe")
    async def probe(caller: Caller, reason: str) -> list:
        seen.append(DB.current_transaction.get())
        return []

    await probe(caller_for(household["wouter"]), "")
    assert seen == [None]


async def test_reason_never_reaches_the_app_log(
    household, logging_on, registry, caplog
):
    caplog.set_level(logging.DEBUG)

    @method("probe")
    async def probe(caller: Caller, reason: str) -> dict:
        raise Boom("no such engram")

    me = caller_for(household["wouter"])
    with pytest.raises(Boom):
        await probe(me, "diagnosis XYZ")
    [row] = await _rows(me)
    assert row["reason"] == "diagnosis XYZ"
    assert "XYZ" not in caplog.text


async def test_a_dict_counts_its_one_list(household, logging_on, registry):
    @method("probe")
    async def probe(caller: Caller, reason: str, *, shape: str) -> dict:
        if shape == "one list":
            return {"engrams": [1, 2], "note": "x"}
        return {"effect": "x", "token": "t"}

    me = caller_for(household["wouter"])
    await probe(me, "", shape="one list")
    await probe(me, "", shape="no list")
    assert [row["returned"] for row in await _rows(me)] == [2, 1]


def test_method_is_registered_under_its_name(registry):
    @method("probe")
    async def probe(caller: Caller, reason: str) -> list:
        return []

    assert registry == {"probe": probe}
    with pytest.raises(ValueError, match="probe"):

        @method("probe")
        async def again(caller: Caller, reason: str) -> list:
            return []


def test_writer_kinds_match_the_database():
    assert get_args(Writer.__value__) == WRITER_KINDS
    with pytest.raises(ValueError, match="robot"):
        Caller(
            principal=somebody().principal,
            writer="robot",  # type: ignore[arg-type]
            session_id=None,
        )


async def test_token_returns_its_subject(secret):
    me = somebody()
    staging = staged.stage(me, "forget", {"space": "personal", "engram": "a.md"})
    assert staging["expires_in"] == 600
    assert await staged.take(me, "forget", staging["token"]) == {
        "space": "personal",
        "engram": "a.md",
    }


async def test_token_refused_for_other_person(secret):
    me = somebody()
    partner = somebody()
    token = staged.stage(me, "forget", {"engram": "a.md"})["token"]
    with pytest.raises(MethodError) as refused:
        await staged.take(partner, "forget", token)
    assert refused.value.code == "stale_token"


async def test_token_refused_for_other_action(secret):
    me = somebody()
    token = staged.stage(me, "forget", {"engram": "a.md"})["token"]
    with pytest.raises(MethodError) as refused:
        await staged.take(me, "instruct", token)
    assert refused.value.code == "stale_token"


async def test_token_refused_after_expiry(secret):
    me = somebody()
    token = staged.stage(me, "forget", {"engram": "a.md"}, now=1_000.0)["token"]
    assert await staged.take(me, "forget", token, now=1_000.0 + 599) == {
        "engram": "a.md"
    }
    with pytest.raises(MethodError) as refused:
        await staged.take(me, "forget", token, now=1_000.0 + 601)
    assert refused.value.code == "stale_token"


@pytest.mark.parametrize(
    "tamper",
    [
        lambda token: token[:-2] + ("AA" if token[-2:] != "AA" else "BB"),
        lambda token: "e30." + token.split(".")[1],
        lambda token: token.replace(".", ""),
        lambda token: token + "é",
        lambda token: token + "\ud800",
        lambda token: "\ud800" + token,
        lambda token: "",
    ],
    ids=[
        "signature",
        "payload",
        "no-dot",
        "non-ascii",
        "surrogate-signature",
        "surrogate-payload",
        "empty",
    ],
)
async def test_token_refused_with_a_bad_signature(secret, tamper):
    me = somebody()
    token = staged.stage(me, "forget", {"engram": "a.md"})["token"]
    with pytest.raises(MethodError) as refused:
        await staged.take(me, "forget", tamper(token))
    assert refused.value.code == "stale_token"


async def test_token_refused_under_another_secret(secret, monkeypatch):
    me = somebody()
    token = staged.stage(me, "forget", {"engram": "a.md"})["token"]
    monkeypatch.setattr(get_settings().web, "session_secret", "t" * 40)
    with pytest.raises(MethodError) as refused:
        await staged.take(me, "forget", token)
    assert refused.value.code == "stale_token"


async def test_token_is_not_signed_with_the_cookie_key(secret):
    # The session cookie and staged tokens share one operator secret; a
    # token signed with it directly must not pass, nor a session cookie.
    me = somebody()
    person_id = str(me.principal.person_id)
    doc = {"pid": person_id, "act": "forget", "sub": {"engram": "a.md"}}
    direct = seal(doc, secret=SECRET, ttl_seconds=600)
    cookie = session.seal(me.principal.person_id, "a@example.test", secret=SECRET)
    for token in (direct, cookie):
        with pytest.raises(MethodError) as refused:
            await staged.take(me, "forget", token)
        assert refused.value.code == "stale_token"


@pytest.mark.parametrize("weak", ["", "short"])
async def test_nothing_is_staged_without_a_strong_secret(monkeypatch, weak):
    monkeypatch.setattr(get_settings().web, "session_secret", weak)
    me = somebody()
    with pytest.raises(MethodError) as refused:
        staged.stage(me, "forget", {"engram": "a.md"})
    assert refused.value.code == "not_configured"
    with pytest.raises(MethodError) as refused:
        await staged.take(me, "forget", "e30.AAAA")
    assert refused.value.code == "not_configured"


def test_method_error_has_a_plain_message():
    error = MethodError("stale_token")
    assert error.code == "stale_token"
    assert error.message and "stale_token" not in error.message
    assert str(error) == error.message
    assert MethodError("entry_too_long", "Too long.").message == "Too long."
