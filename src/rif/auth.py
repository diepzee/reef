import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rif.access import AccessDenied, Principal
from rif.models import Person


async def principal_from_claims(session: AsyncSession, claims: dict) -> Principal:
    """Resolve a principal from verified token claims.

    The durable identity is the provider ``sub``; a verified email is only the
    one-time binding mechanism, because emails are mutable and subjects are
    not. The persons table is the allowlist — an unknown identity is denied,
    and this must never grow into a signup path.

    :param session: database session
    :param claims: verified claims from the access token
    :raises AccessDenied: for missing/unknown subject with no bindable email
    :returns: the authenticated principal
    """
    subject = claims.get("sub")
    if not subject:
        raise AccessDenied("token carries no subject")
    person = await session.scalar(select(Person).where(Person.subject == subject))
    if person is None:
        email = claims.get("email")
        if not email or not claims.get("email_verified"):
            raise AccessDenied("unknown subject and no verified email to bind")
        person = await session.scalar(
            select(Person).where(Person.email == email.lower(), Person.subject.is_(None)))
        if person is None:
            raise AccessDenied(f"not on the allowlist: {email}")
        person.subject = subject
        await session.flush()
    return Principal(person_id=person.id, email=person.email)


async def current_principal(session: AsyncSession) -> Principal:
    """Resolve the principal for the current request.

    HTTP mode reads verified claims from the FastMCP access token. Stdio mode
    (no PORT set) falls back to ``RIF_DEV_PRINCIPAL_EMAIL`` for local
    development; the fallback is dead code in production by construction.

    :param session: database session
    :raises AccessDenied: if no identity can be established
    :returns: the authenticated principal
    """
    if os.environ.get("PORT"):
        from fastmcp.server.dependencies import get_access_token

        return await principal_from_claims(session, dict(get_access_token().claims))
    email = os.environ.get("RIF_DEV_PRINCIPAL_EMAIL")
    if not email:
        raise AccessDenied("no principal on this connection")
    person = await session.scalar(select(Person).where(Person.email == email))
    if person is None:
        raise AccessDenied(f"unknown principal: {email}")
    return Principal(person_id=person.id, email=person.email)
