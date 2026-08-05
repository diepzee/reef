import asyncio
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

import boto3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rif.access import Principal, resolve_space
from rif.config import get_settings
from rif.models import Attachment, AttachmentStatus, Page


class ObjectStore(Protocol):
    """Blob storage for attachment bytes."""

    async def put(self, key: str, data: bytes, mime: str) -> None:
        """Store bytes under a key."""

    async def signed_url(self, key: str, expires_in: int) -> str:
        """Return a time-limited URL for a key."""


class S3ObjectStore:
    """R2 via the S3 API; blocking boto3 calls offloaded from the event loop."""

    def __init__(self) -> None:
        settings = get_settings()
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key)

    async def put(self, key: str, data: bytes, mime: str) -> None:
        """Store bytes under a key.

        :param key: object key
        :param data: raw bytes
        :param mime: content type
        """
        await asyncio.to_thread(
            self._client.put_object, Bucket=self._bucket, Key=key,
            Body=data, ContentType=mime)

    async def signed_url(self, key: str, expires_in: int) -> str:
        """Return a presigned URL.

        :param key: object key
        :param expires_in: lifetime in seconds
        :returns: the URL
        """
        return await asyncio.to_thread(
            self._client.generate_presigned_url, "get_object",
            Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_in)


async def add_attachment(
    session: AsyncSession,
    principal: Principal,
    alias: str,
    data: bytes,
    mime: str,
    *,
    description: str,
    store: ObjectStore,
    page_path: str | None = None,
) -> Attachment:
    """Store an image: pending row, bytes to storage, then flip to ready.

    The mandatory description is what makes images usable in a load-everything
    design -- bytes cannot go into context every turn, but descriptions can.
    A crash mid-upload leaves a pending row that context loading ignores,
    never a ready row with missing bytes.

    :param session: database session
    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :param data: image bytes
    :param mime: content type
    :param description: text description, always required
    :param store: object store to write to
    :param page_path: page in the same space to associate with, if any
    :returns: the stored attachment, status READY
    """
    space = await resolve_space(session, principal, alias)
    page_id = None
    if page_path is not None:
        page = await session.scalar(select(Page).where(
            Page.space_id == space.id, Page.path == page_path))
        page_id = page.id if page else None
    key = f"{space.id}/{uuid4().hex}-{sha256(data).hexdigest()[:16]}"
    attachment = Attachment(space_id=space.id, page_id=page_id, object_key=key,
                            mime=mime, byte_size=len(data),
                            description=description,
                            status=AttachmentStatus.PENDING)
    session.add(attachment)
    await session.flush()
    await store.put(key, data, mime)
    attachment.status = AttachmentStatus.READY
    await session.flush()
    return attachment


async def get_attachment(
    session: AsyncSession, principal: Principal, alias: str, key: str
) -> Attachment | None:
    """Fetch attachment metadata, scoped to a space the principal can see.

    :param session: database session
    :param principal: the authenticated person
    :param alias: ``personal`` or ``household``
    :param key: object key
    :returns: the attachment, or None
    """
    space = await resolve_space(session, principal, alias)
    return await session.scalar(select(Attachment).where(
        Attachment.space_id == space.id, Attachment.object_key == key))
