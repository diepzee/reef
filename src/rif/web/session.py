"""Signed session tokens for the browser surface.

Format: ``b64url(json payload) . b64url(hmac_sha256(secret, payload))``.
Stdlib only: the token is a MAC over a tiny JSON document, which is all a
session cookie needs — no encryption (contents are non-secret), no new
dependency to vet.
"""

import base64
import hmac
import json
import time
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

SESSION_TTL_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class SessionData:
    """The verified contents of a session token."""

    person_id: UUID
    email: str
    expires_at: float


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload, sha256).digest())


def seal(
    person_id: UUID,
    email: str,
    *,
    secret: str,
    now: float | None = None,
    ttl_seconds: int = SESSION_TTL_SECONDS,
) -> str:
    """Produce a signed session token.

    :param person_id: the person's id
    :param email: the person's email
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :param ttl_seconds: lifetime from now
    :returns: the token string
    """
    issued = time.time() if now is None else now
    payload = json.dumps(
        {"pid": str(person_id), "email": email, "exp": issued + ttl_seconds},
        separators=(",", ":"),
    ).encode()
    return f"{_b64(payload)}.{_sign(payload, secret)}"


def unseal(token: str, *, secret: str, now: float | None = None) -> SessionData | None:
    """Verify a token and return its contents, or None.

    None for any defect — bad format, bad signature, expired — because the
    caller's only decision is "session or no session".

    :param token: the token string
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :returns: the session data, or None
    """
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        return None
    try:
        payload = _unb64(parts[0])
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(_sign(payload, secret), parts[1]):
        return None
    try:
        doc = json.loads(payload)
        data = SessionData(UUID(doc["pid"]), doc["email"], float(doc["exp"]))
    except (ValueError, KeyError, TypeError):
        return None
    current = time.time() if now is None else now
    if current >= data.expires_at:
        return None
    return data
