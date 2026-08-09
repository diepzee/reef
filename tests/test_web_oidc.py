"""The sid-claim peek used to build the upstream logout URL."""

import base64
import json

from rif.web.oidc import token_sid


def _jwt(payload: dict) -> str:
    """Assemble an unsigned JWT-shaped token around ``payload``."""

    def seg(doc: dict) -> str:
        raw = json.dumps(doc, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{seg({'alg': 'RS256'})}.{seg(payload)}.fakesig"


def test_token_sid_extracts_claim():
    """A JWT access token's sid claim comes back out."""
    assert token_sid(_jwt({"sub": "user_1", "sid": "ses_abc"})) == "ses_abc"


def test_token_sid_none_when_claim_missing():
    """A JWT without a sid claim yields None."""
    assert token_sid(_jwt({"sub": "user_1"})) is None


def test_token_sid_none_for_opaque_token():
    """A non-JWT (opaque) access token yields None, not an exception."""
    assert token_sid("fake-access-token") is None
    assert token_sid("") is None
    assert token_sid("a.%%%not-base64%%%.c") is None


def test_token_sid_none_for_non_string_claim():
    """A sid claim that isn't a string yields None."""
    assert token_sid(_jwt({"sid": 42})) is None
