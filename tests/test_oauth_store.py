"""The OAuth proxy's state store: encrypted at rest, durable across restarts."""

from reef.oauth_store import build_oauth_store

KEY = "ab" * 32  # 64-char hex, same shape as REEF_SESSION_SECRET


async def test_values_are_encrypted_at_rest(tmp_path):
    """A secret written through the store never appears in the file bytes.

    This is the trap the whole module exists to avoid: FastMCP only wraps
    its own default store in Fernet encryption. If this test can find the
    plaintext on disk, refresh tokens would be readable there too.
    """
    store = build_oauth_store(str(tmp_path / "oauth"), KEY)
    secret = "workos-refresh-token-hunter2"
    await store.put("token-1", {"refresh_token": secret}, collection="tokens")

    files = [p for p in (tmp_path / "oauth").rglob("*") if p.is_file()]
    assert files, "the store wrote nothing to the given directory"
    blob = b"".join(p.read_bytes() for p in files)
    assert secret.encode() not in blob

    assert await store.get("token-1", collection="tokens") == {"refresh_token": secret}


async def test_survives_a_process_restart(tmp_path):
    """A second store over the same dir and key reads the first one's writes.

    This is the durability property the Railway volume buys: deploys must
    not orphan client registrations, so the key derivation has to be
    deterministic from the signing key alone.
    """
    first = build_oauth_store(str(tmp_path / "oauth"), KEY)
    await first.put("client-1", {"client_id": "abc"}, collection="clients")

    second = build_oauth_store(str(tmp_path / "oauth"), KEY)
    assert await second.get("client-1", collection="clients") == {"client_id": "abc"}


async def test_wrong_key_reads_nothing_rather_than_crashing(tmp_path):
    """A rotated key turns old entries into cache misses, not 500s.

    ``raise_on_decryption_error=False`` mirrors FastMCP's default: key
    rotation means everyone re-registers, which the design accepts.
    """
    first = build_oauth_store(str(tmp_path / "oauth"), KEY)
    await first.put("client-1", {"client_id": "abc"}, collection="clients")

    rotated = build_oauth_store(str(tmp_path / "oauth"), "cd" * 32)
    assert await rotated.get("client-1", collection="clients") is None
