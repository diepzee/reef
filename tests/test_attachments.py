import pytest

from rif.access import AccessDenied, Principal
from rif.attachments import add_attachment, get_attachment
from rif.models import AttachmentStatus
from rif.pages import save_page


class FakeStore:
    """In-memory object store standing in for R2 during tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, mime: str) -> None:
        self.objects[key] = data

    async def signed_url(self, key: str, expires_in: int) -> str:
        return f"https://example.test/{key}?ttl={expires_in}"


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_upload_stores_bytes_and_description_and_marks_ready(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "household", "house.md", "boiler", message="x")
    store = FakeStore()
    attachment = await add_attachment(
        session, me, "household", b"\x89PNG fake", "image/png",
        description="the boiler's model plate", store=store, page_path="house.md")
    assert attachment.status is AttachmentStatus.READY
    assert attachment.description == "the boiler's model plate"
    assert store.objects[attachment.object_key] == b"\x89PNG fake"
    assert attachment.page_id is not None


async def test_personal_attachment_is_invisible_to_the_other_person(session, household):
    mine = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    attachment = await add_attachment(
        session, mine, "personal", b"bytes", "image/png",
        description="a private photo", store=FakeStore())
    assert await get_attachment(session, theirs, "personal", attachment.object_key) is None


async def test_unknown_alias_is_denied(session, household):
    me = principal_for(household["wouter"])
    with pytest.raises(AccessDenied):
        await add_attachment(session, me, "theirs", b"x", "image/png",
                             description="x", store=FakeStore())
