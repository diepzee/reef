"""Signed session tokens for the browser surface.

Format: ``b64url(json payload) . b64url(hmac_sha256(secret, payload))``.
Stdlib only: the token is a MAC over a tiny JSON document, which is all a
session cookie needs — no encryption (contents are non-secret), no new
dependency to vet.

A signed cookie is not a row, so there is nothing to delete when one has to
stop working. Two mechanisms stand in for that, and they answer different
questions:

**Revocation** — the payload carries the person's ``session_epoch``, and the
request path compares it against the column. Bumping the column invalidates
every token sealed before the bump, at once, everywhere.

**Expiry that cannot be outrun** — every response re-seals a fresh 7-day
token (the sliding renewal that keeps an active person signed in), so ``exp``
alone never arrives for a cookie in daily use. A stolen one used daily was
therefore valid forever. The payload carries ``iat``, the moment the *first*
token in the chain was issued, and renewal copies it forward rather than
resetting it; past :data:`SESSION_MAX_LIFETIME_SECONDS` from that instant the
chain ends regardless of how recently it was used.
"""

import base64
import hmac
import json
import time
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

SESSION_TTL_SECONDS = 7 * 24 * 3600

SESSION_MAX_LIFETIME_SECONDS = 30 * 24 * 3600
"""How long a chain of renewals may run before a fresh sign-in is required.

Counted from the first token's ``iat``, never reset by renewal. Thirty days
is long enough that an ordinary person meets it rarely, and short enough that
a cookie stolen and never noticed stops working by itself.
"""


@dataclass(frozen=True)
class SessionData:
    """The verified contents of a session token."""

    person_id: UUID
    email: str
    expires_at: float
    sid: str | None = None
    issued_at: float | None = None
    epoch: int = 0


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
    issued_at: float | None = None,
    epoch: int = 0,
) -> str:
    """Produce a signed session token.

    :param person_id: the person's id
    :param email: the person's email
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :param ttl_seconds: lifetime from now
    :param sid: the upstream AuthKit session id, when known; carried so
        logout can end that session too
    :param issued_at: when the *first* token in this renewal chain was
        issued. Renewal must pass the previous token's value through, or the
        absolute ceiling resets on every request and never arrives. Defaults
        to now, which is correct only for a genuinely new session.
    :param epoch: the person's ``session_epoch`` at sign-in, checked against
        the column on every request so a bump ends this token
    :returns: the token string
    """
    issued = time.time() if now is None else now
    doc: dict = {
        "pid": str(person_id),
        "email": email,
        "exp": issued + ttl_seconds,
        "iat": issued if issued_at is None else issued_at,
        "epc": epoch,
    }
    if sid is not None:
        doc["sid"] = sid
    payload = json.dumps(doc, separators=(",", ":")).encode()
    return f"{_b64(payload)}.{_sign(payload, secret)}"


def unseal(token: str, *, secret: str, now: float | None = None) -> SessionData | None:
    """Verify a token and return its contents, or None.

    None for any defect — bad format, bad signature, expired, or past the
    absolute ceiling — because the caller's only decision is "session or no
    session". The epoch is *returned* rather than checked here: this module
    has no database, and the comparison belongs where the person is looked
    up (see :func:`rif.web.requests.principal_from_request`).

    A token sealed before ``iat`` existed has none, and is treated as a
    session whose chain started now rather than being refused — the ceiling
    then applies from this moment on. That keeps a deploy from signing
    everybody out at once, and every renewal after it carries a real value.

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
        issued_at = doc.get("iat")
        epoch = doc.get("epc", 0)
        if not isinstance(pid_str, str) or not isinstance(email, str):
            return None
        if sid is not None and not isinstance(sid, str):
            return None
        if issued_at is not None and not isinstance(issued_at, (int, float)):
            return None
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            return None
        data = SessionData(
            UUID(pid_str),
            email,
            float(exp),
            sid,
            None if issued_at is None else float(issued_at),
            epoch,
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    current = time.time() if now is None else now
    if current >= data.expires_at:
        return None
    if (
        data.issued_at is not None
        and current >= data.issued_at + SESSION_MAX_LIFETIME_SECONDS
    ):
        return None
    return data
