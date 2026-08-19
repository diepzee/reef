"""Identity lookups that run before a principal exists.

Resolving who the caller is necessarily precedes arming the RLS principal --
that is the chicken-and-egg at the centre of the whole design. Ordinary
queries would leave the pre-auth path able to run any ``WHERE`` it likes over
``persons``; these four wrappers narrow it to exact-key lookups through
``SECURITY DEFINER`` functions (see :func:`reef.rls.identity_statements`),
each returning at most one row and only the columns a principal is built
from.

Nothing else in the codebase should query ``persons`` before arming. When
``persons`` gains its policies, anything that does will return nothing
instead of failing loudly, which is the failure mode worth designing against.
"""

from dataclasses import dataclass
from uuid import UUID

from reef.models import Person


@dataclass(frozen=True)
class IdentityRow:
    """The three columns a :class:`reef.access.Principal` is built from."""

    person_id: UUID
    email: str
    display_name: str


def _one(rows: list[dict]) -> IdentityRow | None:
    """Return the single identity row, or None when the lookup matched nothing.

    :param rows: rows as returned by the definer function
    :returns: the identity, or None
    """
    if not rows:
        return None
    row = rows[0]
    return IdentityRow(
        person_id=row["person_id"],
        email=row["person_email"],
        display_name=row["person_display_name"],
    )


async def person_by_subject(subject: str) -> IdentityRow | None:
    """Resolve a person by their provider subject.

    :param subject: the provider's durable subject claim
    :returns: the identity, or None if no person carries that subject
    """
    return _one(
        await Person.raw(
            "SELECT * FROM rif_person_by_subject({})",
            subject,
        )
    )


async def person_by_email(email: str) -> IdentityRow | None:
    """Resolve a person by email address.

    Used only by the development and CLI fallbacks, which have no token to
    read a subject from. Lowercasing happens inside the function so every
    caller normalises identically.

    :param email: the address to look up
    :returns: the identity, or None if no person has that address
    """
    return _one(await Person.raw("SELECT * FROM rif_person_by_email({})", email))


async def bind_subject(email: str, subject: str) -> IdentityRow | None:
    """Bind a provider subject to the invited person with this address, once.

    The lookup and the write are one statement, so two first sign-ins racing
    on the same invitation cannot both succeed: the second matches no row,
    because the first has already filled ``subject`` in.

    :param email: the verified address from the token
    :param subject: the provider's durable subject claim
    :returns: the newly bound identity, or None if no unbound row matched
    """
    return _one(
        await Person.raw("SELECT * FROM rif_person_bind({}, {})", email, subject)
    )


async def person_exists(person_id: UUID) -> bool:
    """Report whether a person row still exists.

    The web session cookie is signed and can outlive the person it names, so
    the request path confirms the row is still there before trusting it. Only
    a boolean crosses the boundary -- the caller already knows the id, and
    needs nothing else.

    :param person_id: the id sealed into the session cookie
    :returns: True if the row exists
    """
    rows = await Person.raw("SELECT rif_person_alive({}) AS alive", person_id)
    return bool(rows and rows[0]["alive"])


async def person_session_epoch(person_id: UUID) -> int | None:
    """Return a person's current session epoch, or None if they are gone.

    Folds the two questions the request path asks of a signed cookie into
    one round trip: the row may have been deleted since the token was
    sealed, and the epoch may have moved past it. Both answers deny, so a
    missing row and a stale epoch are deliberately indistinguishable to the
    caller.

    :param person_id: the id sealed into the session cookie
    :returns: the epoch, or None when no such person exists
    """
    rows = await Person.raw("SELECT rif_person_session_epoch({}) AS epoch", person_id)
    if not rows:
        return None
    return rows[0]["epoch"]


async def revoke_sessions(person_id: UUID) -> None:
    """End every session this person holds, by moving their epoch on.

    Runs under the caller's own policies -- ``persons_self_update`` permits
    exactly this and nothing wider -- so the principal must be armed and can
    only ever revoke themselves.

    :param person_id: the person whose sessions end
    """
    await Person.update({Person.session_epoch: Person.session_epoch + 1}).where(
        Person.id == person_id
    )
