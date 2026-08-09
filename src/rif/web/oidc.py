"""OIDC against the AuthKit domain: code exchange, userinfo, and the
short-lived signed cookie that carries the PKCE verifier across the
redirect to AuthKit and back.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Protocol

import httpx

OAUTH_COOKIE_TTL_SECONDS = 600


class OIDCError(Exception):
    """The upstream token or userinfo call failed."""


class OIDCClient(Protocol):
    """What the callback route needs from an OIDC upstream.

    Tests satisfy this structurally with a fake that never touches the
    network; production wires in :class:`AuthKitOIDC`.
    """

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> str:
        """Exchange an authorization code for an access token.

        :param code: the authorization code
        :param verifier: the PKCE verifier from the login step
        :param redirect_uri: must match the authorize request exactly
        :raises OIDCError: on any upstream failure
        :returns: the access token
        """
        ...

    async def userinfo(self, access_token: str) -> dict:
        """Fetch the OIDC userinfo claims.

        :param access_token: the bearer token from :meth:`exchange`
        :raises OIDCError: on any upstream failure
        :returns: the claims document
        """
        ...


def authkit_domain() -> str:
    """Return the AuthKit base URL with an https scheme.

    :returns: e.g. ``https://foo.authkit.app``, or ``""`` if unconfigured
    """
    domain = os.environ.get("WORKOS_AUTHKIT_DOMAIN", "")
    if domain and not domain.startswith("http"):
        domain = f"https://{domain}"
    return domain.rstrip("/")


def token_sid(access_token: str) -> str | None:
    """Peek the ``sid`` claim out of a JWT access token, if there is one.

    No signature check: the token just arrived over TLS straight from the
    token endpoint, and the claim is only used to build the upstream logout
    URL — WorkOS validates the session id on its own side. Opaque or
    malformed tokens simply yield None, which downgrades logout to
    clearing the local session only.

    :param access_token: the bearer token from the code exchange
    :returns: the sid claim, or None when absent or unreadable
    """
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (ValueError, TypeError):
        return None
    sid = claims.get("sid") if isinstance(claims, dict) else None
    return sid if isinstance(sid, str) else None


def pkce_pair() -> tuple[str, str]:
    """Return a fresh (verifier, challenge) PKCE pair.

    :returns: verifier and its S256 challenge
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _b64(data: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    """Decode a URL-safe base64 string (with or without padding) to bytes."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes, secret: str) -> str:
    """Produce a URL-safe base64-encoded HMAC-SHA256 signature."""
    return _b64(hmac.new(secret.encode(), payload, hashlib.sha256).digest())


def _seal_oauth(
    state: str, verifier: str, *, secret: str, now: float | None = None
) -> str:
    """Seal the login-time state and PKCE verifier into a signed cookie value.

    Mirrors ``rif.web.session.seal``'s HMAC format but with its own short
    (10-minute) lifetime and its own tiny payload shape, since it protects a
    different, much shorter-lived secret than a person session.

    :param state: the CSRF state issued at login
    :param verifier: the PKCE verifier issued at login
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :returns: the token string
    """
    issued = time.time() if now is None else now
    payload = json.dumps(
        {"st": state, "vf": verifier, "exp": issued + OAUTH_COOKIE_TTL_SECONDS},
        separators=(",", ":"),
    ).encode()
    return f"{_b64(payload)}.{_sign(payload, secret)}"


def _unseal_oauth(
    token: str, *, secret: str, now: float | None = None
) -> tuple[str, str] | None:
    """Verify a sealed oauth cookie and return its (state, verifier), or None.

    None for any defect -- bad format, bad signature, expired -- because the
    caller's only decision is whether the callback may proceed.

    :param token: the cookie value
    :param secret: the signing secret
    :param now: clock override for tests; defaults to wall time
    :returns: the (state, verifier) pair, or None
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
        state = doc["st"]
        verifier = doc["vf"]
        exp = doc["exp"]
        if not isinstance(state, str) or not isinstance(verifier, str):
            return None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    current = time.time() if now is None else now
    if current >= float(exp):
        return None
    return state, verifier


class AuthKitOIDC:
    """The real OIDC client; tests substitute a fake via the protocol."""

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> str:
        """Exchange an authorization code for an access token.

        :param code: the authorization code
        :param verifier: the PKCE verifier from the login step
        :param redirect_uri: must match the authorize request exactly
        :raises OIDCError: on any upstream failure
        :returns: the access token
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{authkit_domain()}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": os.environ.get("WORKOS_CLIENT_ID", ""),
                    "code_verifier": verifier,
                    "redirect_uri": redirect_uri,
                },
            )
        if response.status_code != 200:
            raise OIDCError(f"token endpoint returned {response.status_code}")
        token = response.json().get("access_token")
        if not token:
            raise OIDCError("token response carried no access_token")
        return token

    async def userinfo(self, access_token: str) -> dict:
        """Fetch the OIDC userinfo claims.

        :param access_token: the bearer token from :meth:`exchange`
        :raises OIDCError: on any upstream failure
        :returns: the claims document
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{authkit_domain()}/oauth2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 200:
            raise OIDCError(f"userinfo returned {response.status_code}")
        return response.json()
