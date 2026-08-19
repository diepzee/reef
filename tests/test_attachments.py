"""Attachment upload, ACL inheritance, and the mid-flow commit boundary."""

import pytest

from reef.access import AccessDenied, Principal, arm
from reef.attachments import (
    INLINE_MIMES,
    _delivery,
    add_attachment,
    delete_attachment,
    get_attachment,
)
from reef.db import transaction_scope
from reef.models import Attachment, AttachmentStatus
from reef.pages import save_page


class FakeStore:
    """In-memory object store standing in for R2 during tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, mime: str) -> None:
        """Record the bytes under a key.

        :param key: object key
        :param data: raw bytes
        :param mime: content type, ignored here
        """
        self.objects[key] = data

    async def signed_url(
        self, key: str, expires_in: int, *, mime: str = "", filename: str = ""
    ) -> str:
        """Return a fake presigned URL.

        :param key: object key
        :param expires_in: lifetime in seconds
        :param mime: the stored content type, as the real store takes
        :param filename: the stored filename, as the real store takes
        :returns: a deterministic stand-in URL
        """
        return f"https://example.test/{key}?ttl={expires_in}"

    async def get(self, key: str) -> bytes:
        """Return stored bytes for export tests and object-store parity."""
        return self.objects[key]

    async def delete(self, key: str) -> None:
        """Discard the bytes under a key, idempotently.

        :param key: object key
        """
        self.objects.pop(key, None)


def principal_for(person) -> Principal:
    """Build a principal from a seeded person row.

    :param person: a Person row
    :returns: the matching principal
    """
    return Principal(person_id=person.id, email=person.email)


async def test_upload_stores_bytes_and_description_and_marks_ready(household):
    me = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(me, "household", "house.md", "boiler", message="x")
    store = FakeStore()
    attachment = await add_attachment(
        me,
        "household",
        b"\x89PNG fake",
        "image/png",
        filename="boiler.png",
        description="the boiler's model plate",
        store=store,
        page_path="house.md",
    )
    assert attachment.status == AttachmentStatus.READY
    assert attachment.description == "the boiler's model plate"
    assert attachment.filename == "boiler.png"
    assert store.objects[attachment.object_key] == b"\x89PNG fake"
    assert attachment.page_id is not None


async def test_personal_attachment_is_invisible_to_the_other_person(household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    attachment = await add_attachment(
        mine,
        "personal",
        b"bytes",
        "image/png",
        description="a private photo",
        store=FakeStore(),
    )
    async with transaction_scope():
        assert await get_attachment(theirs, "personal", attachment.object_key) is None


async def test_delete_removes_both_the_row_and_the_bytes(household):
    """The whole point: no dangling index entry, no orphaned object."""
    me = principal_for(household["wouter"])
    store = FakeStore()
    attachment = await add_attachment(
        me, "personal", b"bytes", "image/png", description="a photo", store=store
    )
    key = attachment.object_key

    assert await delete_attachment(me, "personal", key, store=store) is True
    assert key not in store.objects
    async with transaction_scope():
        assert await get_attachment(me, "personal", key) is None


async def test_delete_of_a_missing_key_reports_not_found(household):
    """A retry after a successful delete must not look like a fresh success."""
    me = principal_for(household["wouter"])
    assert (
        await delete_attachment(me, "personal", "attachments/nope", FakeStore())
        is False
    )


async def test_cannot_delete_the_other_persons_image(household):
    """Deletion is an access-controlled write, not just a read you act on.

    The bytes must survive too: a delete that failed the ACL but still
    reached the object store would destroy data it was denied.
    """
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    store = FakeStore()
    attachment = await add_attachment(
        mine, "personal", b"bytes", "image/png", description="private", store=store
    )
    key = attachment.object_key

    assert await delete_attachment(theirs, "personal", key, store=store) is False
    assert store.objects[key] == b"bytes"
    async with transaction_scope():
        assert await get_attachment(mine, "personal", key) is not None


async def test_unknown_alias_is_denied(household):
    me = principal_for(household["wouter"])
    with pytest.raises(AccessDenied):
        await add_attachment(
            me, "theirs", b"x", "image/png", description="x", store=FakeStore()
        )


async def test_ready_flip_survives_a_real_commit_boundary(household):
    """add_attachment must re-arm RLS after its own mid-flow commit.

    ``set_config('app.person_id', ..., true)`` is transaction-local, so the
    commit that makes the pending row durable also clears the principal. If
    the second transaction did not re-arm, FORCE RLS would hide the pending
    row, the READY flip would update nothing, and the attachment would stay
    invisible to every reader forever.

    This test deliberately does not use the ``tx`` fixture: the point is the
    real commit between add_attachment's two transactions, which an
    enclosing transaction would swallow.
    """
    me = principal_for(household["wouter"])
    attachment = await add_attachment(
        me,
        "personal",
        b"real-commit-bytes",
        "image/png",
        description="commit-boundary probe",
        store=FakeStore(),
    )

    # Read back in a fresh transaction: READY must be in the database, not
    # merely on the in-memory object add_attachment returned.
    async with transaction_scope():
        await arm(me)
        stored = (
            await Attachment.objects()
            .where(
                Attachment.object_key == attachment.object_key,
                Attachment.status == AttachmentStatus.READY.value,
            )
            .first()
        )
    assert stored is not None


@pytest.mark.parametrize("mime", sorted(INLINE_MIMES))
async def test_raster_images_are_delivered_inline(mime):
    """The web app embeds these with an <img>, so they must render in place."""
    content_type, disposition = _delivery(mime, "photo.png")
    assert content_type == mime
    assert disposition.startswith("inline;")


@pytest.mark.parametrize(
    "mime", ["text/html", "image/svg+xml", "application/xhtml+xml", "text/plain"]
)
async def test_scriptable_and_unknown_types_are_delivered_as_downloads(mime):
    """A caller-chosen content type must not decide how a reader's browser
    treats the bytes: add_file will happily store a 'receipt.pdf' declared as
    text/html, and inline delivery would turn that into a live page."""
    content_type, disposition = _delivery(mime, "receipt.pdf")
    assert content_type == "application/octet-stream"
    assert disposition == 'attachment; filename="receipt.pdf"'


async def test_a_filename_cannot_break_out_of_the_disposition_header():
    _, disposition = _delivery("application/pdf", 'in"; x=y\r\nX-Evil: 1')
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.count('"') == 2


async def test_a_pending_attachment_is_not_readable(household):
    """Its bytes may never have reached the bucket, so a signed URL for it is
    a link that 404s -- which reads as reef losing a file."""
    me = Principal(person_id=household["wouter"].id, email=household["wouter"].email)
    async with transaction_scope():
        await arm(me)
        await Attachment(
            space_id=household["w_personal"].id,
            object_key="attachments/never-uploaded",
            filename="ghost.pdf",
            mime="application/pdf",
            byte_size=1,
            description="bytes that never landed",
            status=AttachmentStatus.PENDING.value,
        ).save()

    async with transaction_scope():
        assert (
            await get_attachment(me, "personal", "attachments/never-uploaded") is None
        )


class BrokenStore(FakeStore):
    """An object store whose uploads always fail."""

    async def put(self, key: str, data: bytes, mime: str) -> None:
        """Fail the way a bucket outage does.

        :param key: object key
        :param data: raw bytes
        :param mime: content type
        :raises RuntimeError: always
        """
        raise RuntimeError("bucket unreachable")


async def test_a_failed_upload_marks_the_row_failed_rather_than_leaking_pending(
    household,
):
    """Left PENDING it is indistinguishable from an upload still in flight,
    so it sits there forever -- invisible to readers, cleanable by nobody."""
    me = Principal(person_id=household["wouter"].id, email=household["wouter"].email)

    with pytest.raises(RuntimeError, match="bucket unreachable"):
        await add_attachment(
            me,
            "personal",
            b"bytes",
            "application/pdf",
            filename="doomed.pdf",
            description="never lands",
            store=BrokenStore(),
        )

    async with transaction_scope():
        await arm(me)
        rows = await Attachment.objects().where(
            Attachment.space_id == household["w_personal"].id
        )
    assert [row.status for row in rows] == [AttachmentStatus.FAILED.value]
    # And it stays unreadable, exactly as the pending row was.
    async with transaction_scope():
        assert await get_attachment(me, "personal", rows[0].object_key) is None
