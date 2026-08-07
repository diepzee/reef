"""Image attachments: metadata in Postgres, bytes in object storage.

:func:`add_attachment` is the one function in this codebase that manages its
own transactions rather than running inside a caller's
:func:`rif.db.transaction_scope`. It has to: the pending row must be
*committed* before the bytes are written, and a commit is what ends a
transaction. Callers must therefore not wrap it.
"""

import asyncio
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

import boto3

from rif.access import Principal, arm, resolve_space
from rif.config import get_settings
from rif.db import transaction_scope
from rif.models import Attachment, AttachmentStatus, Page


class ObjectStore(Protocol):
    """Blob storage for attachment bytes."""

    async def put(self, key: str, data: bytes, mime: str) -> None:
        """Store bytes under a key."""

    async def signed_url(self, key: str, expires_in: int) -> str:
        """Return a time-limited URL for a key."""

    async def delete(self, key: str) -> None:
        """Remove the bytes stored under a key."""


class S3ObjectStore:
    """R2 via the S3 API; blocking boto3 calls offloaded from the event loop."""

    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            # R2 signs against the pseudo-region "auto". Pinned rather than
            # left to boto3's resolution, which reads AWS_REGION and
            # ~/.aws/config: a developer machine with either set signs with a
            # different region than the container, which has neither and falls
            # back to us-east-1. Same code, two signatures, one of them only
            # failing in production.
            region_name="auto",
        )

    async def put(self, key: str, data: bytes, mime: str) -> None:
        """Store bytes under a key.

        :param key: object key
        :param data: raw bytes
        :param mime: content type
        """
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=mime,
        )

    async def signed_url(self, key: str, expires_in: int) -> str:
        """Return a presigned URL.

        :param key: object key
        :param expires_in: lifetime in seconds
        :returns: the URL
        """
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    async def delete(self, key: str) -> None:
        """Remove the object stored under a key.

        S3 delete is idempotent -- removing a key that is already gone
        succeeds -- which is what the caller wants after a partial failure.

        :param key: object key
        """
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self._bucket, Key=key
        )


async def add_attachment(
    principal: Principal,
    alias: str,
    data: bytes,
    mime: str,
    *,
    description: str,
    store: ObjectStore,
    page_path: str | None = None,
) -> Attachment:
    """Store an image: pending row committed, bytes to storage, then flip to ready.

    The mandatory description is what makes images usable in a load-everything
    design — bytes cannot go into context every turn, but descriptions can.
    The pending row is committed on its own before the bytes ever reach
    storage, so a crash at any point after that — including the narrow
    window right after ``store.put`` succeeds but before the ready flip
    commits — leaves, at worst, a pending row (invisible to the readers,
    which filter on ready) and an orphan object; it never loses the row
    entirely and never leaves a ready row with missing bytes. Because the
    principal binding is transaction-local, each of the two transactions
    below arms RLS for itself.

    **Must not be called inside an open transaction** — it opens two.

    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :param data: image bytes
    :param mime: content type
    :param description: text description, always required
    :param store: object store to write to
    :param page_path: page in the same space to associate with, if any
    :returns: the stored attachment, status READY
    """
    async with transaction_scope():
        space = await resolve_space(principal, alias)
        page_id = None
        if page_path is not None:
            page = (
                await Page.objects()
                .where(Page.space_id == space.id, Page.path == page_path)
                .first()
            )
            page_id = page.id if page else None
        # Opaque: never derived from space.id, which must not cross the tool
        # boundary (it would let two keys sharing a prefix reveal they're the
        # same space, an internal-identifier correlation leak).
        #
        # The constant "attachments/" prefix is safe for that reason too --
        # every attachment carries it, so it distinguishes nothing. It exists
        # because R2 has no object versioning; the protection it does offer,
        # bucket locks, is scoped by prefix, and attachments and backups need
        # opposite policies: attachments locked indefinitely, backups only
        # long enough to age out. Without a prefix here, any lock rule covers
        # the whole bucket and no dump can ever be expired.
        key = f"attachments/{uuid4().hex}-{sha256(data).hexdigest()[:16]}"
        attachment = Attachment(
            space_id=space.id,
            page_id=page_id,
            object_key=key,
            mime=mime,
            byte_size=len(data),
            description=description,
            status=AttachmentStatus.PENDING.value,
        )
        await attachment.save()

    await store.put(key, data, mime)

    # A second transaction, because the first one had to commit. The RLS
    # principal was bound transaction-locally and is therefore gone; without
    # re-arming, FORCE RLS hides the pending row and the flip updates nothing.
    async with transaction_scope():
        await arm(principal)
        attachment.status = AttachmentStatus.READY.value
        await attachment.save()
    return attachment


async def delete_attachment(
    principal: Principal, alias: str, key: str, store: ObjectStore
) -> bool:
    """Remove an attachment's row and its bytes.

    The row is deleted and committed *before* the object, deliberately. The
    two stores cannot be made atomic, so the choice is which half-failure to
    prefer:

    - object first: a crash leaves a row whose bytes 404. The index advertises
      an image that cannot be fetched, and nothing detects it until something
      reaches for the pixels. This happened once by hand on 7 Aug 2026.
    - row first: a crash leaves bytes with no row. They cost storage and
      nothing else -- unreachable, since keys are opaque and only ever read
      back through the metadata that no longer exists.

    The second is strictly the safer wreckage, so the row goes first.

    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :param key: object key
    :param store: object store to delete the bytes from
    :returns: True if an attachment was removed, False if it was not found
    """
    async with transaction_scope():
        await arm(principal)
        space = await resolve_space(principal, alias)
        existing = (
            await Attachment.objects()
            .where(Attachment.space_id == space.id, Attachment.object_key == key)
            .first()
        )
        if existing is None:
            return False
        await Attachment.delete().where(
            Attachment.space_id == space.id, Attachment.object_key == key
        )

    await store.delete(key)
    return True


async def get_attachment(
    principal: Principal, alias: str, key: str
) -> Attachment | None:
    """Fetch attachment metadata, scoped to a space the principal can see.

    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :param key: object key
    :returns: the attachment, or None
    """
    space = await resolve_space(principal, alias)
    return (
        await Attachment.objects()
        .where(Attachment.space_id == space.id, Attachment.object_key == key)
        .first()
    )
