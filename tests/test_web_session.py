"""Session cookie sealing: round-trip, tamper, expiry."""

from uuid import uuid4

from rif.web.session import SESSION_TTL_SECONDS, seal, unseal

SECRET = "test-secret"


def test_round_trip():
    pid = uuid4()
    token = seal(pid, "a@b.com", secret=SECRET, now=1000.0)
    data = unseal(token, secret=SECRET, now=1000.0)
    assert data is not None
    assert data.person_id == pid
    assert data.email == "a@b.com"
    assert data.expires_at == 1000.0 + SESSION_TTL_SECONDS


def test_tampered_signature_rejected():
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    payload, sig = token.rsplit(".", 1)
    assert unseal(payload + "." + "x" * len(sig), secret=SECRET, now=1000.0) is None


def test_tampered_payload_rejected():
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    _, sig = token.rsplit(".", 1)
    other = seal(uuid4(), "evil@b.com", secret=SECRET, now=1000.0)
    payload, _ = other.rsplit(".", 1)
    assert unseal(payload + "." + sig, secret=SECRET, now=1000.0) is None


def test_wrong_secret_rejected():
    token = seal(uuid4(), "a@b.com", secret=SECRET)
    assert unseal(token, secret="other") is None


def test_expired_rejected():
    token = seal(uuid4(), "a@b.com", secret=SECRET, now=1000.0)
    assert unseal(token, secret=SECRET, now=1000.0 + SESSION_TTL_SECONDS + 1) is None


def test_garbage_rejected():
    assert unseal("not-a-token", secret=SECRET) is None
    assert unseal("", secret=SECRET) is None
    assert unseal("a.b.c.d", secret=SECRET) is None
