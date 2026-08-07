"""Attachment upload, ACL inheritance, and the mid-flow commit boundary."""

import pytest

from rif.access import AccessDenied, Principal, arm
from rif.attachments import add_attachment, delete_attachment, get_attachment
from rif.db import transaction_scope
from rif.models import Attachment, AttachmentStatus
from rif.pages import save_page


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

    async def signed_url(self, key: str, expires_in: int) -> str:
        """Return a fake presigned URL.

        :param key: object key
        :param expires_in: lifetime in seconds
        :returns: a deterministic stand-in URL
        """
        return f"https://example.test/{key}?ttl={expires_in}"

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
        description="the boiler's model plate",
        store=store,
        page_path="house.md",
    )
    assert attachment.status == AttachmentStatus.READY
    assert attachment.description == "the boiler's model plate"
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
