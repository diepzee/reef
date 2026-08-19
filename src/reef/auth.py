"""Identity resolution: verified token claims to a seeded principal."""

import os

from reef.access import AccessDenied, Principal, arm
from reef.config import env
from reef.identity import bind_subject, person_by_email, person_by_subject
from reef.spaces import ensure_personal_space


async def principal_from_claims(claims: dict) -> Principal:
    """Resolve a principal from verified token claims.

    The durable identity is the provider ``sub``; a verified email is only the
    one-time binding mechanism, because emails are mutable and subjects are
    not. The persons table is still the gate, but its rows are now created by
    invitation at runtime, not by migration. An unknown identity is denied
    exactly as before: a token whose email no member ever invited never gets
    in — invitation-only, never open signup. First sign-in binds the
    provider subject and onboards a personal space with starter pages. The
    ``email_verified`` claim must be the boolean ``True``, not merely truthy.

    Both lookups go through ``reef.identity``, so this function -- the one
    place that runs before any principal exists -- can fetch one row by exact
    key and nothing else.

    The principal is armed the moment it is known, before onboarding. That
    ordering is load-bearing: ``ensure_personal_space`` inserts a space and a
    membership, and once those tables carry policies the inserts are checked
    against the armed principal. Arming afterwards would deny them, and the
    symptom would be every first sign-in failing.

    :param claims: verified claims from the access token
    :raises AccessDenied: for missing/unknown subject with no bindable email
    :returns: the authenticated principal
    """
    subject = claims.get("sub")
    if not subject:
        raise AccessDenied("token carries no subject")
    identity = await person_by_subject(subject)
    if identity is not None:
        principal = Principal(person_id=identity.person_id, email=identity.email)
        await arm(principal)
        return principal

    email = claims.get("email")
    # Exactly True, not merely truthy: a provider that renders the claim
    # as the string "false" would otherwise bind an unverified address.
    if not email or claims.get("email_verified") is not True:
        raise AccessDenied("unknown subject and no verified email to bind")
    # Lookup and write in one statement: two first sign-ins racing on the same
    # invitation cannot both bind, because the loser matches no unbound row.
    identity = await bind_subject(email, subject)
    if identity is None:
        raise AccessDenied(f"not on the allowlist: {email}")
    principal = Principal(person_id=identity.person_id, email=identity.email)
    await arm(principal)
    await ensure_personal_space(identity.person_id, identity.email)
    return principal


async def current_principal() -> Principal:
    """Resolve the principal for the current request.

    HTTP mode reads verified claims from the FastMCP access token. A missing
    token in HTTP mode is denied unless ``REEF_DEV_INSECURE=1``, mirroring
    ``main()``'s startup guard; with that flag set, a tokenless HTTP
    connection falls through to the same dev-email lookup stdio mode uses.
    Stdio mode (no PORT set) always uses ``REEF_DEV_PRINCIPAL_EMAIL`` for
    local development; that fallback is dead code in production by
    construction.

    :raises AccessDenied: if no identity can be established
    :returns: the authenticated principal
    """
    if os.environ.get("PORT"):
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
        if token is not None:
            return await principal_from_claims(dict(token.claims))
        if env("DEV_INSECURE") != "1":
            raise AccessDenied("no access token on this connection")
        # No token and the insecure flag is set: fall through to the same
        # dev-email fallback stdio mode uses.
    email = env("DEV_PRINCIPAL_EMAIL")
    if not email:
        raise AccessDenied("no principal on this connection")
    identity = await person_by_email(email)
    if identity is None:
        raise AccessDenied(f"unknown principal: {email}")
    principal = Principal(person_id=identity.person_id, email=identity.email)
    await arm(principal)
    return principal
