"""Sealed documents: a small JSON document signed so it comes back unchanged.

Format: ``b64url(json payload) . b64url(hmac_sha256(secret, payload))``, the
scheme :mod:`reef.web.session` uses for the session cookie, without its
session fields. The payload carries ``exp``, the moment the token stops
working. Nothing is encrypted: whoever holds a token can read it, so put
nothing secret in it. What the signature guarantees is that the document is
the one this reef sealed, under this secret, and not yet expired.

Stdlib only, and no state: a token is checked by its signature and its
expiry alone, so there is nothing to store and nothing to delete.
"""

import base64
import hmac
import json
import time
from hashlib import sha256


def _b64(data: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding.

    :param data: the bytes to encode
    :returns: the encoded text
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    """Decode URL-safe base64, with or without padding.

    :param text: the encoded text
    :returns: the decoded bytes
    :raises ValueError: when the text is not URL-safe base64
    """
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes, secret: str) -> str:
    """Sign a payload with HMAC-SHA256.

    :param payload: the bytes to sign
    :param secret: the signing secret
    :returns: the signature, URL-safe base64 encoded
    """
    return _b64(hmac.new(secret.encode(), payload, sha256).digest())


def seal(doc: dict, *, secret: str, ttl_seconds: int, now: float | None = None) -> str:
    """Sign a document so it can travel and come back unchanged.

    :param doc: the document; JSON-serialisable, and without an ``exp`` key,
        which this function sets
    :param secret: the signing secret
    :param ttl_seconds: how long the token works, from now
    :param now: clock override for tests; defaults to wall time
    :returns: the token
    :raises ValueError: when the document already has an ``exp`` key
    """
    if "exp" in doc:
        raise ValueError("a sealed document may not set its own 'exp'")
    issued = time.time() if now is None else now
    payload = json.dumps(
        {**doc, "exp": issued + ttl_seconds}, separators=(",", ":"), sort_keys=True
    ).encode()
    return f"{_b64(payload)}.{_sign(payload, secret)}"


def unseal(token: str, *, secret: str, now: float | None = None) -> dict | None:
    """Verify a token and give back the document that was sealed.

    None for any defect: a malformed token, a bad signature, a payload that
    is not a document with a numeric ``exp``, or an expired token. The caller
    has one decision to make, so it gets one answer.

    :param token: the token
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :returns: the document as it was sealed, without ``exp``, or None
    """
    encoded, dot, signature = token.rpartition(".")
    if not dot:
        return None
    try:
        payload = _unb64(encoded)
    except ValueError:
        return None
    # Compared as bytes: compare_digest refuses a str with non-ASCII text.
    expected = _sign(payload, secret).encode()
    if not hmac.compare_digest(expected, signature.encode()):
        return None
    try:
        doc = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    expires = doc.pop("exp", None)
    if isinstance(expires, bool) or not isinstance(expires, int | float):
        return None
    current = time.time() if now is None else now
    if current >= expires:
        return None
    return doc
