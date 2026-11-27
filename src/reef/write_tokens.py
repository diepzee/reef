"""Record a yes for a replacement in the transaction it authorizes."""

from uuid import uuid4

from reef.access import arm
from reef.methods import Caller, MethodError
from reef.models import UsedWriteToken
from reef.sealing import payload_digest


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


async def purge() -> None:
    """Delete used tokens older than a day without returning rows or a count.

    Needs no principal. Run inside the worker's own transaction scope.
    """
    await UsedWriteToken.raw("SELECT reef_purge_write_tokens()")
