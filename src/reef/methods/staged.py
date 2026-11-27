"""Staged actions: a first call describes the effect, a second one acts.

``share``, ``forget`` and ``instruct`` change what other people see, destroy
something, or steer assistants. Each takes two calls. The first returns a
plain description of the effect and a token from :func:`stage`; the person
reads the effect and says yes; the second call brings the token back, and
:func:`take` gives the method the subject it staged.

**What a token is bound to.** The person, the action and an expiry
:data:`TOKEN_TTL_SECONDS` seconds after issue, sealed with
:mod:`reef.sealing` under a key derived from ``REEF_SESSION_SECRET`` for
this purpose alone, so a session cookie can never pass for a staged token,
nor the other way round. :func:`take` refuses a
token for another person, another action, after expiry, or with a bad
signature, and does nothing else. The subject travels in the token, so a
method compares it with the second call's arguments.

**Reset.** A token carries its issue time ``iat``. When the caller's test
account was reset after that, the token is stale, so a confirmation can
never act on freshly reloaded memory.

**Single use.** :func:`take` validates without consuming. ``write`` calls
:func:`consume` to record the signed payload's digest in its write
transaction; ``share`` keeps its own consumed nonce, and ``forget`` binds the
engram's id so a new engram cannot reuse consent for a deleted one.

**The token is readable.** It is signed, not encrypted: whoever holds it can
read the subject. Stage paths and digests, never engram text.
"""

import hmac
import time
from hashlib import sha256
from uuid import uuid4

from reef.access import arm
from reef.config import get_settings
from reef.db import transaction_scope
from reef.limits import MIN_SECRET_LENGTH
from reef.methods import Caller, MethodError
from reef.models import Person, UsedWriteToken
from reef.sealing import payload_digest, seal, unseal

TOKEN_TTL_SECONDS = 600
"""How long a staged token works: ten minutes, like the share nonce."""

_PURPOSE = b"reef.methods.staged"
"""What the derived key is for; it keeps staged tokens apart from cookies."""


def _secret() -> str:
    """Return the key staged tokens are signed with, or refuse to sign.

    The key is derived from ``REEF_SESSION_SECRET``, which also signs the
    session cookie: one secret for the operator to set, two keys that
    cannot stand in for each other. An empty or short secret would let
    anyone forge a token, so nothing is staged or taken under one.

    :returns: the derived key
    :raises MethodError: ``not_configured``, when the secret is too short
    """
    secret = get_settings().web.session_secret
    if len(secret) < MIN_SECRET_LENGTH:
        raise MethodError("not_configured")
    return hmac.new(secret.encode(), _PURPOSE, sha256).hexdigest()


def next_step(confirm_argument: str) -> str:
    """Tell the assistant how to confirm after the person says yes.

    :param confirm_argument: the argument that takes the token
    :returns: the next step for the same call
    """
    return (
        "Ask the person first. After a yes, send this same call again with every "
        f"argument unchanged, and add `{confirm_argument}` with this token."
    )


def stage(
    caller: Caller,
    action: str,
    subject: dict,
    *,
    confirm_argument: str = "confirm",
    now: float | None = None,
) -> dict:
    """Issue a token for an action the caller will confirm.

    :param caller: who is staging; only they can take the token
    :param action: the action, such as ``forget``; only it can take the token
    :param subject: what the action is about, such as the space and engram;
        JSON-serialisable and readable by whoever holds the token
    :param confirm_argument: the argument that takes the token on the next call
    :param now: clock override for tests; defaults to wall time
    :returns: ``token``, ``expires_in`` in seconds, and ``next``, how to confirm
    :raises MethodError: ``not_configured``, when the secret is too short
    """
    issued = time.time() if now is None else now
    doc = {
        "pid": str(caller.principal.person_id),
        "act": action,
        "sub": subject,
        "iat": issued,
    }
    token = seal(doc, secret=_secret(), ttl_seconds=TOKEN_TTL_SECONDS, now=issued)
    return {
        "token": token,
        "expires_in": TOKEN_TTL_SECONDS,
        "next": next_step(confirm_argument),
    }


async def _reset_epoch(caller: Caller) -> float | None:
    """Read when the caller's test account was last reset.

    The query names the caller's row itself. Row-level security also shows a
    person only their own ``test_accounts`` row, and there is none for people
    who are not test accounts.

    :param caller: whose reset to read
    :returns: the reset time in epoch seconds, or None when never reset or
        not a test account
    """
    async with transaction_scope():
        await arm(caller.principal)
        rows = await Person.raw(
            "SELECT extract(epoch FROM reset_at)::float8 AS reset "
            "FROM test_accounts WHERE person_id = {}",
            caller.principal.person_id,
        )
    return rows[0]["reset"] if rows else None


async def take(
    caller: Caller, action: str, token: str, *, now: float | None = None
) -> dict:
    """Check a token and give back the subject it was staged with.

    :param caller: who is confirming; must be who staged
    :param action: the action being confirmed; must be the one staged
    :param token: the token from :func:`stage`
    :param now: clock override for tests; defaults to wall time
    :returns: the subject passed to :func:`stage`
    :raises MethodError: ``stale_token``, for a token that is expired, badly
        signed, or for another person or action; ``not_configured``, when
        the secret is too short
    """
    doc = unseal(token, secret=_secret(), now=now)
    if (
        doc is None
        or doc.get("pid") != str(caller.principal.person_id)
        or doc.get("act") != action
        or not isinstance(doc.get("sub"), dict)
    ):
        raise MethodError("stale_token")
    reset = await _reset_epoch(caller)
    iat = doc.get("iat")
    if reset is not None and (not isinstance(iat, int | float) or reset > iat):
        raise MethodError("stale_token")
    return doc["sub"]


async def consume(caller: Caller, token: str) -> None:
    """Remember a checked token, or refuse it when already used.

    Call only after signature, person and subject checks, inside the write
    transaction. The unique insert takes concurrent uses in turn, and a
    rollback restores the token along with the engrams. Hash the signed
    payload bytes so alternate spellings spend the same record.

    :param caller: the person whose token has been checked
    :param token: the checked token from the first call
    :raises MethodError: ``used_token`` when already used
    """
    await arm(caller.principal)
    inserted = await UsedWriteToken.raw(
        "INSERT INTO used_write_tokens (id, person_id, token_digest) "
        "VALUES ({}, {}, {}) ON CONFLICT (person_id, token_digest) "
        "DO NOTHING RETURNING id",
        uuid4(),
        caller.principal.person_id,
        payload_digest(token),
    )
    if not inserted:
        raise MethodError("used_token")
