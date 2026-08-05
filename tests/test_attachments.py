import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from rif.access import AccessDenied, Principal
from rif.attachments import add_attachment, get_attachment
from rif.models import (
    Attachment,
    AttachmentStatus,
    Membership,
    Person,
    Space,
    SpaceKind,
)
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


async def test_ready_flip_survives_a_real_commit_boundary(engine):
    """add_attachment must re-arm RLS after its own mid-flow commit.

    ``set_config('app.person_id', :pid, true)`` is transaction-local, so
    the commit that makes the pending row durable also clears the RLS
    principal. Under a production ``session_scope`` session the READY flip
    then runs in a fresh transaction where FORCE RLS hides the row, the
    UPDATE matches nothing, and SQLAlchemy raises StaleDataError. The
    savepoint-bound ``session`` fixture can never catch this — its
    ``commit()`` is only a savepoint release — so this test uses a real
    committing session and cleans up after itself.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        person = Person(email="commit-boundary@example.test", display_name="C")
        session.add(person)
        await session.flush()
        space = Space(slug="commit-boundary", kind=SpaceKind.PERSONAL,
                      owner_person_id=person.id)
        session.add(space)
        await session.flush()
        session.add(Membership(person_id=person.id, space_id=space.id))
        await session.commit()
        # Plain values for cleanup: rollback expires ORM objects, and touching
        # an expired attribute on an AsyncSession raises MissingGreenlet.
        person_id, space_id = person.id, space.id
        me = Principal(person_id=person_id, email=person.email)
        try:
            attachment = await add_attachment(
                session, me, "personal", b"real-commit-bytes", "image/png",
                description="commit-boundary probe", store=FakeStore())
            await session.commit()
            # Re-arm and read back: the READY status must be in the database,
            # not just on the in-memory object.
            await session.execute(
                text("SELECT set_config('app.person_id', :pid, true)"),
                {"pid": str(person_id)})
            stored = await session.scalar(select(Attachment).where(
                Attachment.object_key == attachment.object_key,
                Attachment.status == AttachmentStatus.READY))
            assert stored is not None
        finally:
            await session.rollback()
            await session.execute(
                text("SELECT set_config('app.person_id', :pid, true)"),
                {"pid": str(person_id)})
            await session.execute(
                delete(Attachment).where(Attachment.space_id == space_id))
            await session.execute(
                delete(Membership).where(Membership.person_id == person_id))
            await session.execute(delete(Space).where(Space.id == space_id))
            await session.execute(delete(Person).where(Person.id == person_id))
            await session.commit()
