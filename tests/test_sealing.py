"""Sealed documents: what comes out is what went in, until it expires."""

import base64
import hashlib
import hmac
import json

import pytest

from reef.sealing import seal, unseal

SECRET = "s" * 40


def _forge(doc: object) -> str:
    """Sign any JSON value the way :func:`seal` signs, for defect tests."""
    payload = json.dumps(doc).encode()
    signature = hmac.new(SECRET.encode(), payload, hashlib.sha256).digest()
    encode = base64.urlsafe_b64encode
    return f"{encode(payload).decode().rstrip('=')}.{encode(signature).decode().rstrip('=')}"


def test_round_trip_gives_back_the_document():
    doc = {"pid": "p", "act": "forget", "sub": {"engram": "a.md", "n": 2}}
    token = seal(doc, secret=SECRET, ttl_seconds=60, now=1_000.0)
    assert unseal(token, secret=SECRET, now=1_000.0) == doc


def test_expires_after_its_lifetime():
    token = seal({"x": 1}, secret=SECRET, ttl_seconds=60, now=1_000.0)
    assert unseal(token, secret=SECRET, now=1_059.0) == {"x": 1}
    assert unseal(token, secret=SECRET, now=1_060.0) is None


def test_a_signed_document_without_a_usable_expiry_is_refused():
    assert unseal(_forge([1, 2]), secret=SECRET, now=0.0) is None
    assert unseal(_forge({"x": 1}), secret=SECRET, now=0.0) is None
    assert unseal(_forge({"exp": True}), secret=SECRET, now=0.0) is None
    assert unseal(_forge({"exp": "later"}), secret=SECRET, now=0.0) is None
    assert unseal(_forge({"exp": 10, "x": 1}), secret=SECRET, now=0.0) == {"x": 1}


def test_a_document_may_not_set_its_own_expiry():
    with pytest.raises(ValueError, match="exp"):
        seal({"exp": 10**12}, secret=SECRET, ttl_seconds=60)
