"""File attachments: metadata in Postgres, bytes in object storage.

:func:`add_attachment` is the one function in this codebase that manages its
own transactions rather than running inside a caller's
:func:`rif.db.transaction_scope`. It has to: the pending row must be
*committed* before the bytes are written, and a commit is what ends a
transaction. Callers must therefore not wrap it.
"""

import asyncio
import logging
import re
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

import boto3

from rif.access import Principal, arm, resolve_space, resolve_writable_space
from rif.config import get_settings
from rif.db import transaction_scope
from rif.models import Attachment, AttachmentStatus, Page

logger = logging.getLogger(__name__)

MIME_RE = re.compile(r"[A-Za-z0-9][\w.+-]*/[A-Za-z0-9][\w.+-]*\Z")
"""A content type, and nothing else.

The value is echoed into an S3 ``ContentType`` and into a signed URL's query
string, so it is matched against a shape rather than merely length-checked.
"""

INLINE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}
)
"""Types a browser may render in place, because a page embeds them.

Raster images only. ``image/svg+xml`` is deliberately absent: an SVG is a
document that can carry script, and it is embedded by exactly the same
Markdown syntax as a PNG, so a co-member could hand a reader a drawing that
is really a program. Everything outside this set is delivered as an opaque
download instead -- which is what the ``/api/files`` link surface wants
anyway.
"""

_DOWNLOAD_MIME = "application/octet-stream"


def _delivery(mime: str, filename: str) -> tuple[str, str]:
    """Return the content type and disposition a stored file is served with.

    Bytes arrive with a caller-supplied type and are stored with it, so
    ``add_file`` can be told a ``receipt.pdf`` is ``text/html`` and the
    object store will faithfully serve it as a document. The bucket is a
    different origin from the app, so this is not an XSS into reef -- but it
    is a reader clicking a file their cove described as a receipt and getting
    a live page, which is a good enough phishing primitive to close.

    Overriding at signing time rather than at upload keeps the stored bytes
    and their declared type intact for export, and fixes the existing objects
    too: every fetch goes through a fresh signature.

    :param mime: the stored content type
    :param filename: the stored filename, for the download's name
    :returns: the ``Content-Type`` and ``Content-Disposition`` to sign with
    """
    safe_name = _header_safe(filename) or "download"
    if mime in INLINE_MIMES:
        return mime, f'inline; filename="{safe_name}"'
    return _DOWNLOAD_MIME, f'attachment; filename="{safe_name}"'


def _header_safe(value: str) -> str:
    """Strip what must never reach a ``Content-Disposition`` header.

    Quotes and separators would end the quoted filename early; control
    characters -- carriage return and newline above all -- are the classic
    header-splitting payload. Both are removed rather than escaped, because
    a filename is a label here and nothing depends on it round-tripping.

    :param value: the stored filename
    :returns: the filename with unsafe characters removed
    """
    return "".join(
        char for char in value if ord(char) >= 32 and char not in '"\\;\x7f'
    ).strip()


class ObjectStore(Protocol):
    """Blob storage for attachment bytes."""

    async def put(self, key: str, data: bytes, mime: str) -> None:
        """Store bytes under a key."""

    async def signed_url(
        self, key: str, expires_in: int, *, mime: str = "", filename: str = ""
    ) -> str:
        """Return a time-limited URL for a key, with safe delivery headers."""

    async def get(self, key: str) -> bytes:
        """Return the bytes stored under a key."""

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

    async def signed_url(
        self, key: str, expires_in: int, *, mime: str = "", filename: str = ""
    ) -> str:
        """Return a presigned URL that dictates how the bytes are delivered.

        ``response-content-type`` and ``response-content-disposition`` are
        part of the signature, so a reader cannot strip them off the URL to
        get the stored type back -- an altered query string simply fails to
        verify. See :func:`_delivery` for what is chosen and why.

        Called without a ``mime`` (an export, a caller that has no row) the
        object is delivered as an opaque download, which is the safe default.

        :param key: object key
        :param expires_in: lifetime in seconds
        :param mime: the stored content type, when the caller has the row
        :param filename: the stored filename, for the download's name
        :returns: the URL
        """
        content_type, disposition = _delivery(mime, filename)
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": disposition,
            },
            ExpiresIn=expires_in,
        )

    async def get(self, key: str) -> bytes:
        """Read an object's bytes, primarily for a full data export.

        :param key: object key
        :returns: raw stored bytes
        """

        def read() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            try:
                return body.read()
            finally:
                body.close()

        return await asyncio.to_thread(read)

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
    filename: str = "",
    description: str,
    store: ObjectStore,
    page_path: str | None = None,
) -> Attachment:
    """Store a file: pending row committed, bytes to storage, then flip to ready.

    The mandatory description makes arbitrary files useful in an index-first
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
    :param alias: ``personal`` or a shared-space slug
    :param data: file bytes
    :param mime: content type
    :param filename: original human-readable filename, when known
    :param description: text description, always required
    :param store: object store to write to
    :param page_path: page in the same space to associate with, if any
    :returns: the stored attachment, status READY
    """
    async with transaction_scope():
        space = await resolve_writable_space(principal, alias)
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
            filename=filename,
            mime=mime,
            byte_size=len(data),
            description=description,
            status=AttachmentStatus.PENDING.value,
        )
        await attachment.save()

    try:
        await store.put(key, data, mime)
    except Exception:
        # The row is committed and the bytes are not coming. Left PENDING it
        # is indistinguishable from an upload still in flight, so it sits
        # there forever: invisible to every reader, counted by nothing,
        # cleanable by nobody. FAILED says what happened, and is a state a
        # sweep can act on. Best effort -- if this write fails too the
        # original error is still what the caller must hear, so it is
        # swallowed rather than allowed to mask the cause.
        try:
            async with transaction_scope():
                await arm(principal)
                attachment.status = AttachmentStatus.FAILED.value
                await attachment.save()
        except Exception:
            logger.exception("could not mark attachment %s failed", key)
        raise

    # A second transaction, because the first one had to commit. The RLS
    # principal was bound transaction-locally and is therefore gone; without
    # re-arming, FORCE RLS hides the pending row and the flip updates nothing.
    async with transaction_scope():
        await arm(principal)
        attachment.status = AttachmentStatus.READY.value
        await attachment.save()
    return attachment


async def erase_objects(keys: list[str], store: ObjectStore | None = None) -> None:
    """Best-effort removal of object bytes whose rows are already gone.

    Call this *after* the transaction that removed the rows has committed,
    for the reason :func:`delete_attachment` sets out: the two stores cannot
    be made atomic, and bytes with no row are the safer wreckage. Each key is
    attempted independently and failures are logged rather than raised —
    the rows are gone either way, so raising here would report a failure for
    an operation that already succeeded.

    :param keys: object keys to erase
    :param store: object store; defaults to the real S3 one
    """
    if not keys:
        return
    store = store or S3ObjectStore()
    for key in keys:
        try:
            await store.delete(key)
        except Exception:
            logger.exception("could not remove orphaned object %s", key)


async def delete_attachment(
    principal: Principal, alias: str, key: str, store: ObjectStore
) -> bool:
    """Remove an attachment's row and its bytes.

    The row is deleted and committed *before* the object, deliberately. The
    two stores cannot be made atomic, so the choice is which half-failure to
    prefer:

    - object first: a crash leaves a row whose bytes 404. The index advertises
      a file that cannot be fetched, and nothing detects it until something
      reaches for the pixels. This happened once by hand on 7 Aug 2026.
    - row first: a crash leaves bytes with no row. They cost storage and
      nothing else -- unreachable, since keys are opaque and only ever read
      back through the metadata that no longer exists.

    The second is strictly the safer wreckage, so the row goes first.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param key: object key
    :param store: object store to delete the bytes from
    :returns: True if an attachment was removed, False if it was not found
    """
    async with transaction_scope():
        await arm(principal)
        space = await resolve_writable_space(principal, alias)
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

    READY only, matching every other reader (the index and the context loader
    both filter on it). A PENDING row is one whose bytes have not reached the
    object store yet -- or never did, because the upload failed between the
    two transactions :func:`add_attachment` uses. Handing back a signed URL
    for one produces a link that 404s at the bucket, which reads as reef
    losing a file rather than as an upload that never finished.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param key: object key
    :returns: the attachment, or None
    """
    space = await resolve_space(principal, alias)
    return (
        await Attachment.objects()
        .where(
            Attachment.space_id == space.id,
            Attachment.object_key == key,
            Attachment.status == AttachmentStatus.READY.value,
        )
        .first()
    )
