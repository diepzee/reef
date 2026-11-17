"""Piccolo table definitions for the reef store.

Three schema facts Piccolo cannot express in a table definition, and which
``reef.rls.constraint_statements`` therefore emits as raw DDL: the composite
key on ``memberships`` (Piccolo gives every table one surrogate primary key),
the ``(cove_id, path)`` uniqueness of a page, and the one-personal-cove-per
-person invariant (a *partial* unique index on ``coves.owner_person_id``,
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
    Boolean,
    Bytea,
    ForeignKey,
    Integer,
    OnDelete,
    Real,
    Timestamp,
    Varchar,
)
from piccolo.columns.defaults.timestamp import TimestampNow
from piccolo.table import Table

from reef.db import DB


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


class CoveKind(StrEnum):
    """The two kinds of cove: one private per person, any number shared."""

    PERSONAL = "personal"
    SHARED = "shared"


class MemberRole(StrEnum):
    """What a membership grants. Invites choose; writes require MEMBER."""

    MEMBER = "member"
    VIEWER = "viewer"


class AttachmentStatus(StrEnum):
    """Upload lifecycle of an attachment's bytes."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class Person(Table, tablename="persons", db=DB):
    """A human principal. Provider subject is durable identity; email binds it.

    The avatar lives in the row rather than the object store. It is capped at
    a size where that is cheap (see ``reef.web.routes_api.AVATAR_MAX_BYTES``),
    it is read on nearly every screen so a signed-URL round trip per face
    would be the dominant cost, and keeping it here means it travels with
    ``pg_dump`` — the same "leave with everything" property the pages have.
    Attachments, which are unbounded and rarely read, stay in S3.
    """

    id = UUID(primary_key=True, default=uuid4)
    email = Varchar(unique=True)
    subject = Varchar(null=True, unique=True, default=None)
    display_name = Varchar()
    avatar_mime = Varchar(null=True, default=None)
    avatar_bytes = Bytea(null=True, default=None)
    invited_by_person_id = ForeignKey(
        "self", null=True, default=None, on_delete=OnDelete.set_null
    )
    created_at = Timestamp(default=TimestampNow())
    # Bumped to end every session this person holds. reef issues its own
    # session cookie, which is signed rather than stored, so there is no row
    # to delete when one has to be revoked -- this counter is what a sealed
    # token is checked against instead. See reef.web.session.
    session_epoch = Integer(default=0)
    # Set only by the launch exception in reef.opendoor. A row admitted that
    # way has no inviter, which on its own is indistinguishable from the
    # founding person's row -- this flag is the difference, and it is what
    # the seat ceiling is counted over.
    joined_open_door = Boolean(default=False)
    #: Version whose "what's new" this person has already read, or ``None``
    #: for somebody who has never opened the panel -- including everybody
    #: who predates it, who should see the dot exactly once. A column on
    #: ``persons`` rather than a table of its own: ``persons_self_select``
    #: and ``persons_self_update`` already say "yours and only yours", so
    #: this inherits the rule instead of restating it, and nothing has to
    #: be added to ``enable_statements``.
    last_seen_release = Varchar(null=True, default=None)


class Cove(Table, tablename="coves", db=DB):
    """A group of people. Every cove has one accountable owner.

    ``slug`` is the name its creator chose, kept for provenance and as the
    alias offered to each new member. It is deliberately **not** unique: a
    globally unique cove name is a cross-tenant namespace, which let anyone
    squat ``family`` or ``home`` for everybody and turned a collision into an
    existence oracle. What a person actually addresses a cove by lives on
    their own membership row, unique per person -- see :class:`Membership`.
    """

    id = UUID(primary_key=True, default=uuid4)
    slug = Varchar()
    kind = Varchar(choices=CoveKind)
    # null=False because the migrations made this column NOT NULL and
    # Piccolo's ForeignKey defaults to nullable. Unstated, the schema the
    # tests build is *laxer* than the one production runs, and this repo has
    # already paid once for tests proving a shape production does not have.
    # tests/test_migration_chain.py compares the two and fails on any new
    # instance of it.
    owner_person_id = ForeignKey(Person, null=False)
    version = Integer(default=0)


class Membership(Table, tablename="memberships", db=DB):
    """Who may see which cove, what it grants, and what they call it.

    The real key is ``(person_id, cove_id)``; Piccolo's surrogate ``id`` is
    an artefact, and the composite uniqueness is enforced by a raw
    constraint rather than by this definition.

    ``alias`` is the name this person addresses this cove by, and it is the
    only name the tool surface accepts. It lives here rather than on
    ``coves`` because the constraint that actually has to hold is "the
    aliases *one person* can reach are unique" -- which is
    ``UNIQUE (person_id, alias)``, an ordinary index, and is not expressible
    as a property of the cove. Two people may each have a cove called
    ``family`` without either knowing the other exists.
    """

    person_id = ForeignKey(Person)
    cove_id = ForeignKey(Cove)
    role = Varchar(choices=MemberRole, default=MemberRole.MEMBER.value)
    alias = Varchar()


class Page(Table, tablename="pages", db=DB):
    """A markdown page within a cove, optimistically versioned."""

    id = UUID(primary_key=True, default=uuid4)
    cove_id = ForeignKey(Cove, index=True)
    path = Varchar()
    title = Varchar()
    tags = Array(base_column=Varchar(), default=list)
    body = Varchar(length=None)
    version = Integer(default=0)
    created_at = Timestamp(default=TimestampNow())
    updated_at = Timestamp(default=TimestampNow())
    # What the index shows for this page, derived from ``body`` when the page
    # is saved -- see ``reef.context.index_fields``. They exist so that
    # building the index does not have to read every body.
    #
    # ``indexed_version`` is what makes them trustworthy. They describe the
    # body as it was at that version, and are used only while it equals
    # ``version``. A row written before these columns existed, or by any
    # future writer that forgets them, simply does not match, and the index
    # falls back to deriving the values from the body. Stale is impossible;
    # the worst case is slow.
    description = Varchar(length=None, null=True, default=None)
    size = Integer(null=True, default=None)
    reference_targets = Array(base_column=Varchar(length=None), null=True, default=None)
    indexed_version = Integer(null=True, default=None)
    # The version whose wiki links are in ``links``. Null on a page saved
    # before links were stored; see reef.links.
    linked_version = Integer(null=True, default=None)


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
    cove_id = ForeignKey(Cove, index=True)
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
    # NOT NULL in the migrated schema; see Cove.owner_person_id.
    dest_cove_id = ForeignKey(Cove, null=False)
    dest_path = Varchar()
    section_text = Varchar(length=None, null=True, default=None)
    created_at = Timestamp(default=utc_now)
    consumed_at = Timestamp(null=True, default=None)


class CoveAppearance(Table, tablename="cove_appearances", db=DB):
    """How one person has chosen to see one cove.

    A cove's colour and creature are derived from its alias, and this
    overrides that derivation *for one viewer only* -- two members of the
    same cove can see it differently, and neither can restyle it for the
    other. That is why this is its own table rather than columns on
    ``coves`` or ``memberships``.

    On ``coves`` it would be shared, so changing it would be an act of
    administration and would mean widening the deliberately narrow
    column grant that today lets a member update nothing but ``version``.
    On ``memberships`` it would need a self-update policy on a table that
    also carries ``role`` -- and row security cannot say *which column*, so
    a viewer could promote themselves to member. Here there is simply
    nothing worth escalating to: every column is a preference, so a blanket
    "your own rows" policy is safe.

    Null means "not chosen": the alias-derived value stands. The composite
    uniqueness of ``(person_id, cove_id)`` is a raw constraint, as on
    ``memberships``.
    """

    id = UUID(primary_key=True, default=uuid4)
    # Both NOT NULL in the migrated schema; see Cove.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    cove_id = ForeignKey(Cove, null=False)
    color = Varchar(null=True, default=None)
    glyph = Varchar(null=True, default=None)


class JobStatus(StrEnum):
    """Where a job is in its life."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(Table, tablename="jobs", db=DB):
    """One piece of background work, done on behalf of one person.

    A row says *what* to do and for *whom*, by id only. It never carries
    content: no titles, no text, no paths. ``error`` holds the name of an
    exception class and nothing an exception might have said, because a
    message can quote the thing that failed.

    Under row security like every other table: a person sees and changes
    their own jobs and nobody else's. The worker finds the next job through
    ``reef_claim_job``, the one function allowed to look across people, and
    then does everything else armed as the job's person. See
    :func:`reef.rls.job_statements`.

    ``subject_ids`` names what the job is about. Empty means "everything of
    this person's", and a raw partial unique index keeps at most one such
    job queued per person and kind.
    """

    id = UUID(primary_key=True, default=uuid4)
    # NOT NULL in the migrated schema; see Cove.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    kind = Varchar()
    subject_ids = Array(base_column=UUID(), default=list)
    status = Varchar(choices=JobStatus, default=JobStatus.QUEUED)
    attempts = Integer(default=0)
    run_after = Timestamp(default=TimestampNow())
    locked_by = Varchar(null=True, default=None)
    locked_at = Timestamp(null=True, default=None)
    error = Varchar(null=True, default=None)
    created_at = Timestamp(default=TimestampNow())
    finished_at = Timestamp(null=True, default=None)


class LinkKind(StrEnum):
    """What a link says about its two ends. A closed set."""

    REFERENCES = "references"
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    DERIVED_FROM = "derived_from"
    ABOUT = "about"
    PART_OF = "part_of"


class LinkSource(StrEnum):
    """How a link came to exist."""

    AUTHOR = "author"
    WIKILINK = "wikilink"
    DREAM = "dream"
    IMPORT = "import"


class LinkStatus(StrEnum):
    """Whether a link counts. Only ``active`` links are shown or followed."""

    ACTIVE = "active"
    PROPOSED = "proposed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Link(Table, tablename="links", db=DB):
    """A connection somebody wrote from one page to another.

    Every ``[[wiki link]]`` in a page body becomes one row when the page is
    saved. A link that leaves the body is marked ``superseded`` and kept, so
    the table also says what used to be connected and until when.

    A link is visible only to a reader who can read **both** ends. To anyone
    else it does not exist: not as a link, not as a backlink, not in a
    count. Row security enforces that, and it also refuses a link from a
    shared cove into a personal one. See :func:`reef.rls.link_statements`.

    ``target_text`` is the target exactly as written, for a wiki link. It is
    what decides when the link is retired: by whether those words are still
    in the body, never by who happens to be saving.

    ``author_id`` is the person whose save made the link exist. That is
    usually whoever wrote the words. It is the creator of the *target*
    instead when the words were written first and the page they name came
    later. Treat it as "who caused this row", not "who wrote this link",
    before showing it to anyone.

    ``from_anchor`` and ``to_anchor`` are unused today. They leave room for
    a link that points at a section rather than a whole page.
    """

    id = UUID(primary_key=True, default=uuid4)
    # Both NOT NULL in the migrated schema; see Cove.owner_person_id.
    from_page_id = ForeignKey(Page, null=False)
    to_page_id = ForeignKey(Page, null=False)
    from_anchor = Varchar(null=True, default=None)
    to_anchor = Varchar(null=True, default=None)
    kind = Varchar(choices=LinkKind, default=LinkKind.REFERENCES)
    source = Varchar(choices=LinkSource, default=LinkSource.WIKILINK)
    status = Varchar(choices=LinkStatus, default=LinkStatus.ACTIVE)
    target_text = Varchar(length=None, null=True, default=None)
    confidence = Real(null=True, default=None)
    valid_from = Timestamp(null=True, default=None)
    valid_to = Timestamp(null=True, default=None)
    recorded_at = Timestamp(default=TimestampNow())
    superseded_at = Timestamp(null=True, default=None)
    author_id = ForeignKey(Person, null=True, default=None, on_delete=OnDelete.set_null)


TABLES = [
    Person,
    Cove,
    Membership,
    Page,
    Revision,
    Attachment,
    Promotion,
    CoveAppearance,
    Job,
    Link,
]
