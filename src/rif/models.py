"""Piccolo table definitions for the rif store.

Three schema facts Piccolo cannot express in a table definition, and which
``rif.rls.constraint_statements`` therefore emits as raw DDL: the composite
key on ``memberships`` (Piccolo gives every table one surrogate primary key),
the ``(space_id, path)`` uniqueness of a page, and the one-personal-space-per
-person invariant (a *partial* unique index on ``spaces.owner_person_id``,
which Piccolo has no syntax for). All three are constraints the database must
hold whatever the ORM believes, so they live next to the RLS policy DDL
rather than being dropped.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from piccolo.columns import (
    UUID,
    Array,
    ForeignKey,
    Integer,
    OnDelete,
    Timestamp,
    Varchar,
)
from piccolo.columns.defaults.timestamp import TimestampNow
from piccolo.table import Table

from rif.db import DB


def utc_now() -> datetime:
    """Return the current instant as a naive UTC ``datetime``.

    Every ``Timestamp`` column in this schema is ``TIMESTAMP WITHOUT TIME
    ZONE``. Columns defaulted by the Postgres server take their value from
    the server's ``TimeZone`` setting, not from UTC -- correct only
    incidentally, when the server happens to run UTC. Columns whose value is
    later compared against client-computed time
    (:attr:`Promotion.created_at`, for nonce-expiry checks) use this
    client-side default instead, so the comparison is correct regardless of
    server locale.

    :returns: the current UTC instant, without tzinfo
    """
    return datetime.now(UTC).replace(tzinfo=None)


class SpaceKind(StrEnum):
    """The two kinds of space: one private per person, any number shared."""

    PERSONAL = "personal"
    SHARED = "shared"


class MemberRole(StrEnum):
    """What a membership grants. VIEWER is dormant until invites can grant it."""

    MEMBER = "member"
    VIEWER = "viewer"


class AttachmentStatus(StrEnum):
    """Upload lifecycle of an attachment's bytes."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class Person(Table, tablename="persons", db=DB):
    """A human principal. Provider subject is durable identity; email binds it."""

    id = UUID(primary_key=True, default=uuid4)
    email = Varchar(unique=True)
    subject = Varchar(null=True, unique=True, default=None)
    display_name = Varchar()
    invited_by_person_id = ForeignKey(
        "self", null=True, default=None, on_delete=OnDelete.set_null
    )
    created_at = Timestamp(default=TimestampNow())


class Space(Table, tablename="spaces", db=DB):
    """A named group of people. Every space has one accountable owner."""

    id = UUID(primary_key=True, default=uuid4)
    slug = Varchar(unique=True)
    kind = Varchar(choices=SpaceKind)
    owner_person_id = ForeignKey(Person)
    version = Integer(default=0)


class Membership(Table, tablename="memberships", db=DB):
    """Who may see which space, and what the membership grants.

    The real key is ``(person_id, space_id)``; Piccolo's surrogate ``id`` is
    an artefact, and the composite uniqueness is enforced by a raw
    constraint rather than by this definition.
    """

    person_id = ForeignKey(Person)
    space_id = ForeignKey(Space)
    role = Varchar(choices=MemberRole, default=MemberRole.MEMBER.value)


class Page(Table, tablename="pages", db=DB):
    """A markdown page within a space, optimistically versioned."""

    id = UUID(primary_key=True, default=uuid4)
    space_id = ForeignKey(Space, index=True)
    path = Varchar()
    title = Varchar()
    tags = Array(base_column=Varchar(), default=list)
    body = Varchar(length=None)
    version = Integer(default=0)
    created_at = Timestamp(default=TimestampNow())
    updated_at = Timestamp(default=TimestampNow())


class Revision(Table, tablename="revisions", db=DB):
    """Append-only history: full page state per write, not just the body."""

    id = UUID(primary_key=True, default=uuid4)
    page_id = ForeignKey(Page, index=True)
    path = Varchar()
    title = Varchar()
    tags = Array(base_column=Varchar(), default=list)
    body = Varchar(length=None)
    message = Varchar()
    author_id = ForeignKey(Person, null=True, default=None, on_delete=OnDelete.set_null)
    created_at = Timestamp(default=TimestampNow())


class Attachment(Table, tablename="attachments", db=DB):
    """A file in object storage, described in text for context loading."""

    id = UUID(primary_key=True, default=uuid4)
    space_id = ForeignKey(Space, index=True)
    page_id = ForeignKey(Page, null=True, default=None, on_delete=OnDelete.set_null)
    object_key = Varchar(unique=True)
    filename = Varchar(length=512, default="")
    mime = Varchar()
    byte_size = Integer()
    description = Varchar(length=None)
    status = Varchar(choices=AttachmentStatus, default=AttachmentStatus.PENDING)
    created_at = Timestamp(default=TimestampNow())


class Promotion(Table, tablename="promotions", db=DB):
    """A prepared or completed share. The row is nonce and audit trail.

    ``section_text`` is None for a whole-page share; for a section share it
    holds the exact extracted span, so confirm can verify the disclosure the
    user approved is the text that actually moves.
    """

    id = UUID(primary_key=True, default=uuid4)
    person_id = ForeignKey(Person)
    source_page_id = ForeignKey(Page)
    source_version = Integer()
    dest_space_id = ForeignKey(Space)
    dest_path = Varchar()
    section_text = Varchar(length=None, null=True, default=None)
    created_at = Timestamp(default=utc_now)
    consumed_at = Timestamp(null=True, default=None)


TABLES = [Person, Space, Membership, Page, Revision, Attachment, Promotion]
