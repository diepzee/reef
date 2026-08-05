from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import ARRAY, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Return the current instant as a naive UTC ``datetime``.

    Every ``DateTime`` column in this schema is ``TIMESTAMP WITHOUT TIME
    ZONE``. Columns populated by ``server_default=func.now()`` alone take
    their value from the Postgres server's ``TimeZone`` setting, not from
    UTC -- correct only incidentally, when the server happens to run UTC.
    Columns whose value is later compared against client-computed time
    (:class:`Promotion.created_at`, for nonce-expiry checks) use this
    client-side default instead, so the comparison is correct regardless
    of server locale.
    """
    return datetime.now(UTC).replace(tzinfo=None)


class SpaceKind(StrEnum):
    """The two kinds of space a person can belong to."""

    PERSONAL = "personal"
    HOUSEHOLD = "household"


class AttachmentStatus(StrEnum):
    """Upload lifecycle of an attachment's bytes."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class Base(DeclarativeBase):
    """Declarative base for all rif tables."""


class Person(Base):
    """A human principal. Provider subject is durable identity; email binds it."""

    __tablename__ = "persons"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(unique=True)
    subject: Mapped[str | None] = mapped_column(unique=True)
    display_name: Mapped[str]


class Space(Base):
    """A knowledge layer. Personal spaces have exactly one owner."""

    __tablename__ = "spaces"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[SpaceKind]
    owner_person_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("persons.id"), unique=True
    )
    version: Mapped[int] = mapped_column(default=0)


class Membership(Base):
    """Who may see which space."""

    __tablename__ = "memberships"

    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), primary_key=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.id"), primary_key=True)


class Page(Base):
    """A markdown page within a space, optimistically versioned."""

    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("space_id", "path"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.id"), index=True)
    path: Mapped[str]
    title: Mapped[str]
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    body: Mapped[str]
    version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class Revision(Base):
    """Append-only history: full page state per write, not just the body."""

    __tablename__ = "revisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    page_id: Mapped[UUID] = mapped_column(ForeignKey("pages.id"), index=True)
    path: Mapped[str]
    title: Mapped[str]
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    body: Mapped[str]
    message: Mapped[str]
    author_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Attachment(Base):
    """An image in object storage, described in text for context loading."""

    __tablename__ = "attachments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.id"), index=True)
    page_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pages.id", ondelete="SET NULL")
    )
    object_key: Mapped[str] = mapped_column(unique=True)
    mime: Mapped[str]
    byte_size: Mapped[int]
    description: Mapped[str]
    status: Mapped[AttachmentStatus] = mapped_column(default=AttachmentStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Promotion(Base):
    """A prepared or completed promotion. The row is nonce and audit trail."""

    __tablename__ = "promotions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"))
    source_page_id: Mapped[UUID] = mapped_column(ForeignKey("pages.id"))
    source_version: Mapped[int]
    dest_path: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utc_now, server_default=func.now())
    consumed_at: Mapped[datetime | None]
