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
    sid: str | None = None


def _b64(data: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    """Decode URL-safe base64 string (with or without padding) to bytes."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes, secret: str) -> str:
    """Produce a URL-safe base64-encoded HMAC-SHA256 signature."""
    return _b64(hmac.new(secret.encode(), payload, sha256).digest())


def seal(
    person_id: UUID,
    email: str,
    *,
    secret: str,
    now: float | None = None,
    ttl_seconds: int = SESSION_TTL_SECONDS,
    sid: str | None = None,
) -> str:
    """Produce a signed session token.

    :param person_id: the person's id
    :param email: the person's email
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :param ttl_seconds: lifetime from now
    :param sid: the upstream AuthKit session id, when known; carried so
        logout can end that session too
    :returns: the token string
    """
    issued = time.time() if now is None else now
    doc: dict = {"pid": str(person_id), "email": email, "exp": issued + ttl_seconds}
    if sid is not None:
        doc["sid"] = sid
    payload = json.dumps(doc, separators=(",", ":")).encode()
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
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(payload, secret), parts[1]):
        return None
    try:
        doc = json.loads(payload)
        pid_str = doc["pid"]
        email = doc["email"]
        exp = doc["exp"]
        sid = doc.get("sid")
        if not isinstance(pid_str, str) or not isinstance(email, str):
            return None
        if sid is not None and not isinstance(sid, str):
            return None
        data = SessionData(UUID(pid_str), email, float(exp), sid)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    current = time.time() if now is None else now
    if current >= data.expires_at:
        return None
    return data
