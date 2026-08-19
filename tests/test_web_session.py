"""Session cookie sealing: round-trip, tamper, expiry."""

import base64
import json
from uuid import uuid4

from reef.web.session import (
    SESSION_MAX_LIFETIME_SECONDS,
    SESSION_TTL_SECONDS,
    _sign,
    seal,
    unseal,
)

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


def test_sid_round_trip():
    """A token sealed with a sid carries it back out on unseal."""
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0, sid="ses_123")
    data = unseal(token, secret=SECRET, now=1000.0)
    assert data is not None
    assert data.sid == "ses_123"


def test_sid_absent_is_none():
    """A token sealed without a sid (incl. pre-sid legacy tokens) has sid None."""
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    data = unseal(token, secret=SECRET, now=1000.0)
    assert data is not None
    assert data.sid is None


def test_non_string_sid_rejected():
    """Payload with a numeric sid is rejected."""
    issued = 1000.0
    payload = json.dumps(
        {
            "pid": str(uuid4()),
            "email": "a@b.com",
            "exp": issued + SESSION_TTL_SECONDS,
            "sid": 7,
        },
        separators=(",", ":"),
    ).encode()
    sig = _sign(payload, SECRET)
    token = base64.urlsafe_b64encode(payload).rstrip(b"=").decode() + "." + sig
    assert unseal(token, secret=SECRET, now=issued) is None


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


def test_renewal_cannot_outrun_the_absolute_ceiling():
    """A cookie in daily use used to live forever: every response re-sealed a
    fresh 7-day token, so ``exp`` never arrived. ``iat`` is carried through
    each renewal instead of reset, so the chain ends on time."""
    pid, secret = uuid4(), "s" * 32
    start = 1_000_000.0
    token = seal(pid, "a@b.test", secret=secret, now=start)

    # Renew once a day, exactly as the api() wrapper does on every response.
    clock = start
    for _ in range(29):
        clock += 24 * 3600
        data = unseal(token, secret=secret, now=clock)
        assert data is not None, f"died early at {(clock - start) / 86400:.0f} days"
        token = seal(
            data.person_id,
            data.email,
            secret=secret,
            now=clock,
            issued_at=data.issued_at,
        )

    # Still inside the ceiling, and its own 7-day exp is nowhere near.
    assert unseal(token, secret=secret, now=clock) is not None
    # One renewal past it, the chain is over regardless of how fresh the
    # last token is.
    past = start + SESSION_MAX_LIFETIME_SECONDS + 1
    assert unseal(token, secret=secret, now=past) is None


def test_a_renewal_that_forgets_issued_at_would_never_expire():
    """Names the mistake the ``issued_at`` parameter exists to prevent."""
    pid, secret = uuid4(), "s" * 32
    start = 1_000_000.0
    token = seal(pid, "a@b.test", secret=secret, now=start)
    clock = start
    for _ in range(60):
        clock += 24 * 3600
        data = unseal(token, secret=secret, now=clock)
        assert data is not None
        # Deliberately omitting issued_at: iat resets to now every time.
        token = seal(data.person_id, data.email, secret=secret, now=clock)
    assert clock > start + SESSION_MAX_LIFETIME_SECONDS
    assert unseal(token, secret=secret, now=clock) is not None


def test_a_token_sealed_before_iat_existed_still_verifies():
    """A deploy must not sign everybody out at once."""
    import json

    from reef.web.session import _b64, _sign

    pid, secret = uuid4(), "s" * 32
    payload = json.dumps(
        {"pid": str(pid), "email": "a@b.test", "exp": 2_000_000.0},
        separators=(",", ":"),
    ).encode()
    legacy = f"{_b64(payload)}.{_sign(payload, secret)}"

    data = unseal(legacy, secret=secret, now=1_000_000.0)
    assert data is not None
    assert data.issued_at is None and data.epoch == 0


def test_the_epoch_round_trips_and_is_type_checked():
    pid, secret = uuid4(), "s" * 32
    data = unseal(seal(pid, "a@b.test", secret=secret, epoch=7), secret=secret)
    assert data is not None and data.epoch == 7
