"""Record a yes for a replacement in the transaction it authorizes."""

import base64
from hashlib import sha256
from uuid import uuid4

from reef.access import arm
from reef.methods import Caller, MethodError
from reef.models import UsedWriteToken


async def consume(caller: Caller, token: str) -> None:
    """Remember a checked token, or refuse it when already used.

    Call only after signature, person and subject checks, inside the write
    transaction. The unique insert takes concurrent uses in turn, and a
    rollback restores the token along with the engrams. Reject alternate
    base64 spellings so padding cannot turn one signed token into two digests.

    :param caller: the person whose token has been checked
    :param token: the checked token from the first call
    :raises MethodError: ``stale_token`` when used or spelled differently
    """
    payload = token.rpartition(".")[0]
    canonical = (
        base64.urlsafe_b64encode(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        .rstrip(b"=")
        .decode()
    )
    if payload != canonical:
        raise MethodError("stale_token")
    await arm(caller.principal)
    inserted = await UsedWriteToken.raw(
        "INSERT INTO used_write_tokens (id, person_id, token_digest) "
        "VALUES ({}, {}, {}) ON CONFLICT (person_id, token_digest) "
        "DO NOTHING RETURNING id",
        uuid4(),
        caller.principal.person_id,
        sha256(token.encode()).hexdigest(),
    )
    if not inserted:
        raise MethodError("stale_token")


async def purge() -> None:
    """Delete used tokens older than a day without returning rows or a count.

    Needs no principal. Run inside the worker's own transaction scope.
    """
    await UsedWriteToken.raw("SELECT reef_purge_write_tokens()")
