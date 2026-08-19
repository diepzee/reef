"""Durable, encrypted storage for the OAuth proxy's state.

FastMCP's ``OAuthProxy`` persists DCR client registrations, JTI mappings,
and upstream token sets through an ``AsyncKeyValue`` store. Its *default*
store is encrypted, but lives in a platformdirs data directory -- ephemeral
on Railway, where ``main`` auto-deploys on every push, so every merge would
wipe every connector registration. Supplying our own store fixes the
location but silently loses the encryption: FastMCP only wraps the store it
builds itself. This module therefore mirrors that construction exactly
(see fastmcp ``oauth_proxy/proxy.py``), rooted at the volume-backed
directory and keyed from ``REEF_JWT_SIGNING_KEY`` -- deliberately decoupled
from the WorkOS client secret, so rotating that secret cannot orphan the
store.
"""

from pathlib import Path

from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper


def build_oauth_store(directory: str, signing_key: str) -> AsyncKeyValue:
    """Build the encrypted file store the OAuth proxy persists into.

    Mirrors FastMCP's default store construction -- same file-store class,
    same key-derivation helper, same salt, same treat-decryption-errors-as
    -misses posture -- with the location and key made explicit so both can
    be controlled by configuration and asserted by tests.

    :param directory: filesystem root for the store; created if absent
        (production points this at the Railway volume mount)
    :param signing_key: the high-entropy secret the encryption key is
        derived from; rotating it turns every stored entry into a miss,
        which surfaces as every connector re-registering
    :returns: the store to pass as ``WorkOSProvider(client_storage=...)``
    """
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    encryption_key = derive_jwt_key(
        high_entropy_material=signing_key,
        salt="fastmcp-storage-encryption-key",
    )
    file_store = FileTreeStore(
        data_directory=path,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(path),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(path),
    )
    return FernetEncryptionWrapper(
        key_value=file_store,
        fernet=Fernet(key=encryption_key),
        raise_on_decryption_error=False,
    )
