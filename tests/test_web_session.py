"""Session cookie sealing: round-trip, tamper, expiry."""

import base64
import json
from uuid import uuid4

from rif.web.session import SESSION_TTL_SECONDS, _sign, seal, unseal

SECRET = "test-secret"


def test_round_trip():
    """Valid token round-trip: seal and unseal recover original data."""
    pid = uuid4()
    token = seal(pid, "a@b.com", secret=SECRET, now=1000.0)
    data = unseal(token, secret=SECRET, now=1000.0)
    assert data is not None
    assert data.person_id == pid
    assert data.email == "a@b.com"
    assert data.expires_at == 1000.0 + SESSION_TTL_SECONDS


def test_tampered_signature_rejected():
    """Tampered signature fails verification."""
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    payload, sig = token.rsplit(".", 1)
    assert unseal(payload + "." + "x" * len(sig), secret=SECRET, now=1000.0) is None


def test_tampered_payload_rejected():
    """Payload swapped with different signature is rejected."""
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    _, sig = token.rsplit(".", 1)
    other = seal(uuid4(), "evil@b.com", secret=SECRET, now=1000.0)
    payload, _ = other.rsplit(".", 1)
    assert unseal(payload + "." + sig, secret=SECRET, now=1000.0) is None


def test_wrong_secret_rejected():
    """Token sealed with one secret is rejected with a different secret."""
    token = seal(uuid4(), "a@b.com", secret=SECRET)
    assert unseal(token, secret="other") is None


def test_expired_rejected():
    """Expired token is rejected."""
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    assert unseal(token, secret=SECRET, now=1000.0 + SESSION_TTL_SECONDS + 1) is None


def test_garbage_rejected():
    """Malformed tokens are rejected."""
    assert unseal("not-a-token", secret=SECRET) is None
    assert unseal("", secret=SECRET) is None
    assert unseal("a.b.c.d", secret=SECRET) is None


def test_non_string_pid_rejected():
    """Payload with numeric pid is rejected."""
    issued = 1000.0
    exp = issued + SESSION_TTL_SECONDS
    payload = json.dumps(
        {"pid": 12345, "email": "a@b.com", "exp": exp},
        separators=(",", ":"),
    ).encode()
    sig = _sign(payload, SECRET)
    token = base64.urlsafe_b64encode(payload).rstrip(b"=").decode() + "." + sig
    assert unseal(token, secret=SECRET, now=issued) is None


def test_non_string_email_rejected():
    """Payload with numeric email is rejected."""
    issued = 1000.0
    exp = issued + SESSION_TTL_SECONDS
    payload = json.dumps(
        {"pid": str(uuid4()), "email": 42, "exp": exp},
        separators=(",", ":"),
    ).encode()
    sig = _sign(payload, SECRET)
    token = base64.urlsafe_b64encode(payload).rstrip(b"=").decode() + "." + sig
    assert unseal(token, secret=SECRET, now=issued) is None
