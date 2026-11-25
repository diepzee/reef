"""Piccolo table definitions for the reef store.

Three schema facts Piccolo cannot express in a table definition, and which
``reef.rls.constraint_statements`` therefore emits as raw DDL: the composite
key on ``memberships`` (Piccolo gives every table one surrogate primary key),
the ``(space_id, path)`` uniqueness of an engram, and the one-personal-space-per
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
    Boolean,
    Bytea,
    Column,
    Date,
    ForeignKey,
    Integer,
    OnDelete,
    Real,
    Timestamp,
    Timestamptz,
    Varchar,
)
from piccolo.columns.defaults.timestamp import TimestampNow
from piccolo.columns.defaults.timestamptz import TimestamptzNow
from piccolo.table import Table

from reef.db import DB
from reef.limits import FILENAME_CHARS, MIME_CHARS


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
    """What a membership grants. Invites choose; writes require MEMBER."""

    MEMBER = "member"
    VIEWER = "viewer"


class AttachmentStatus(StrEnum):
    """Upload lifecycle of an attachment's bytes."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class TextStatus(StrEnum):
    """How far Reef got reading the text inside a file.

    Null on the row, not a member here, means the file was never looked at.
    The database holds the same list: ``attachments_text_status_known`` in
    :func:`reef.rls.file_text_statements`.
    """

    WAITING = "waiting"
    READ = "read"
    PARTLY_READ = "partly_read"
    UNREADABLE = "unreadable"
    NOT_READ = "not_read"


class Person(Table, tablename="persons", db=DB):
    """A human principal. Provider subject is durable identity; email binds it.

    The avatar lives in the row rather than the object store. It is capped at
    a size where that is cheap (see ``reef.web.routes_api.AVATAR_MAX_BYTES``),
    it is read on nearly every screen so a signed-URL round trip per face
    would be the dominant cost, and keeping it here means it travels with
    ``pg_dump`` — the same "leave with everything" property the engrams have.
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


class Space(Table, tablename="spaces", db=DB):
    """A group of people. Every space has one accountable owner.

    ``slug`` is the name its creator chose, kept for provenance and as the
    alias offered to each new member. It is deliberately **not** unique: a
    globally unique space name is a cross-tenant namespace, which let anyone
    squat ``family`` or ``home`` for everybody and turned a collision into an
    existence oracle. What a person actually addresses a space by lives on
    their own membership row, unique per person -- see :class:`Membership`.
    """

    id = UUID(primary_key=True, default=uuid4)
    slug = Varchar()
    kind = Varchar(choices=SpaceKind)
    # null=False because the migrations made this column NOT NULL and
    # Piccolo's ForeignKey defaults to nullable. Unstated, the schema the
    # tests build is *laxer* than the one production runs, and this repo has
    # already paid once for tests proving a shape production does not have.
    # tests/test_migration_chain.py compares the two and fails on any new
    # instance of it.
    owner_person_id = ForeignKey(Person, null=False)
    version = Integer(default=0)


class Membership(Table, tablename="memberships", db=DB):
    """Who may see which space, what it grants, and what they call it.

    The real key is ``(person_id, space_id)``; Piccolo's surrogate ``id`` is
    an artefact, and the composite uniqueness is enforced by a raw
    constraint rather than by this definition.

    ``alias`` is the name this person addresses this space by, and it is the
    only name the tool surface accepts. It lives here rather than on
    ``spaces`` because the constraint that actually has to hold is "the
    aliases *one person* can reach are unique" -- which is
    ``UNIQUE (person_id, alias)``, an ordinary index, and is not expressible
    as a property of the space. Two people may each have a space called
    ``family`` without either knowing the other exists.
    """

    person_id = ForeignKey(Person)
    space_id = ForeignKey(Space)
    role = Varchar(choices=MemberRole, default=MemberRole.MEMBER.value)
    alias = Varchar()


class Engram(Table, tablename="engrams", db=DB):
    """A markdown engram within a space, optimistically versioned."""

    id = UUID(primary_key=True, default=uuid4)
    space_id = ForeignKey(Space, index=True)
    path = Varchar()
    title = Varchar()
    tags = Array(base_column=Varchar(), default=list)
    body = Varchar(length=None)
    version = Integer(default=0)
    created_at = Timestamp(default=TimestampNow())
    updated_at = Timestamp(default=TimestampNow())
    # What the index shows for this engram, derived from ``body`` when the engram
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
    # The version whose wiki links are in ``links``. Null on an engram saved
    # before links were stored; see reef.links.
    linked_version = Integer(null=True, default=None)


class Revision(Table, tablename="revisions", db=DB):
    """Append-only history: full engram state per write, not just the body."""

    id = UUID(primary_key=True, default=uuid4)
    engram_id = ForeignKey(Engram, index=True)
    path = Varchar()
    title = Varchar()
    tags = Array(base_column=Varchar(), default=list)
    body = Varchar(length=None)
    message = Varchar()
    author_id = ForeignKey(Person, null=True, default=None, on_delete=OnDelete.set_null)
    # Who made the change: assistant, person or system. Null on history that
    # predates the column.
    channel = Varchar(null=True, default=None)
    created_at = Timestamp(default=TimestampNow())


class Attachment(Table, tablename="attachments", db=DB):
    """A file in object storage, described in text for context loading.

    ``text_status``, ``pages`` and ``text_error`` say how reading its text
    went: see :class:`TextStatus` and :class:`FilePage`. ``text_error`` is
    the name of an error class and nothing an error said, as on
    :class:`Job`.
    """

    id = UUID(primary_key=True, default=uuid4)
    space_id = ForeignKey(Space, index=True)
    engram_id = ForeignKey(Engram, null=True, default=None, on_delete=OnDelete.set_null)
    object_key = Varchar(unique=True)
    filename = Varchar(length=FILENAME_CHARS, default="")
    mime = Varchar(length=MIME_CHARS)
    byte_size = Integer()
    description = Varchar(length=None)
    status = Varchar(choices=AttachmentStatus, default=AttachmentStatus.PENDING)
    created_at = Timestamp(default=TimestampNow())
    text_status = Varchar(choices=TextStatus, null=True, default=None)
    pages = Integer(null=True, default=None)
    text_error = Varchar(length=255, null=True, default=None)


class Promotion(Table, tablename="promotions", db=DB):
    """A prepared or completed share. The row is nonce and audit trail.

    ``section_text`` is None for a whole-engram share; for a section share it
    holds the exact extracted span, so confirm can verify the disclosure the
    user approved is the text that actually moves.
    """

    id = UUID(primary_key=True, default=uuid4)
    person_id = ForeignKey(Person)
    source_engram_id = ForeignKey(Engram)
    source_version = Integer()
    # NOT NULL in the migrated schema; see Space.owner_person_id.
    dest_space_id = ForeignKey(Space, null=False)
    dest_path = Varchar()
    section_text = Varchar(length=None, null=True, default=None)
    created_at = Timestamp(default=utc_now)
    consumed_at = Timestamp(null=True, default=None)


class SpaceAppearance(Table, tablename="space_appearances", db=DB):
    """How one person has chosen to see one space.

    A space's colour and creature are derived from its alias, and this
    overrides that derivation *for one viewer only* -- two members of the
    same space can see it differently, and neither can restyle it for the
    other. That is why this is its own table rather than columns on
    ``spaces`` or ``memberships``.

    On ``spaces`` it would be shared, so changing it would be an act of
    administration and would mean widening the deliberately narrow
    column grant that today lets a member update nothing but ``version``.
    On ``memberships`` it would need a self-update policy on a table that
    also carries ``role`` -- and row security cannot say *which column*, so
    a viewer could promote themselves to member. Here there is simply
    nothing worth escalating to: every column is a preference, so a blanket
    "your own rows" policy is safe.

    Null means "not chosen": the alias-derived value stands. The composite
    uniqueness of ``(person_id, space_id)`` is a raw constraint, as on
    ``memberships``.
    """

    id = UUID(primary_key=True, default=uuid4)
    # Both NOT NULL in the migrated schema; see Space.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    space_id = ForeignKey(Space, null=False)
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
    # NOT NULL in the migrated schema; see Space.owner_person_id.
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
    """A connection somebody wrote from one engram to another.

    Every ``[[wiki link]]`` in an engram body becomes one row when the engram is
    saved. A link that leaves the body is marked ``superseded`` and kept, so
    the table also says what used to be connected and until when.

    A link is visible only to a reader who can read **both** ends. To anyone
    else it does not exist: not as a link, not as a backlink, not in a
    count. Row security enforces that, and it also refuses a link from a
    shared space into a personal one. See :func:`reef.rls.link_statements`.

    ``target_text`` is the target exactly as written, for a wiki link. It is
    what decides when the link is retired: by whether those words are still
    in the body, never by who happens to be saving.

    ``author_id`` is the person whose save made the link exist. That is
    usually whoever wrote the words. It is the creator of the *target*
    instead when the words were written first and the engram they name came
    later. Treat it as "who caused this row", not "who wrote this link",
    before showing it to anyone.

    ``from_anchor`` and ``to_anchor`` are unused today. They leave room for
    a link that points at a section rather than a whole engram.
    """

    id = UUID(primary_key=True, default=uuid4)
    # Both NOT NULL in the migrated schema; see Space.owner_person_id.
    from_engram_id = ForeignKey(Engram, null=False)
    to_engram_id = ForeignKey(Engram, null=False)
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


class AccessKind(StrEnum):
    """How a person used an engram. Only real use is recorded."""

    READ = "read"
    WRITE = "write"
    REMEMBER = "remember"
    WEB = "web"


class EngramAccess(Table, tablename="engram_access", db=DB):
    """One use of one engram by one person: the usage log.

    Recorded when a person, or an assistant acting for them, opens or
    writes an engram. Appearing in a search result or in the index is not use
    and is not recorded.

    Strictly per person. One member's reading habits in a shared space are
    not another member's business, so a person sees their own rows and
    nobody else's. See :func:`reef.rls.usage_statements`.

    ``session_id`` groups the uses of one conversation. It is a digest of
    the transport's session id, never the id itself, and is null where the
    transport has no session. ``weight`` is how much the use counts toward
    activation.
    """

    id = UUID(primary_key=True, default=uuid4)
    # Both NOT NULL in the migrated schema; see Space.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    engram_id = ForeignKey(Engram, null=False)
    via = Varchar(choices=AccessKind, default=AccessKind.READ)
    weight = Real(default=1.0)
    session_id = Varchar(null=True, default=None)
    accessed_at = Timestamp(default=TimestampNow())


class SearchLog(Table, tablename="search_log", db=DB):
    """One search by one person: what they asked and which engrams came back.

    Kept to measure search on real questions. Together with the usage log
    it shows a miss: an engram the person opened in the same session that the
    search before it did not return.

    Strictly per person, like the usage log. The query text is what a person
    asked their own memory, so a person sees their own rows and nobody
    else's. See :func:`reef.rls.search_log_statements`.

    ``engram_ids`` holds the engrams returned, best first. It is a plain array,
    not a set of foreign keys: a deleted engram leaves its id behind, which
    names nothing. Rows older than :data:`reef.searchlog.RETENTION_DAYS`
    days are deleted.
    """

    id = UUID(primary_key=True, default=uuid4)
    # NOT NULL in the migrated schema; see Space.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    query = Varchar(length=None)
    engram_ids = Array(base_column=UUID())
    session_id = Varchar(null=True, default=None)
    searched_at = Timestamp(default=TimestampNow())


class CallLog(Table, tablename="call_log", db=DB):
    """One call to one method by one person's assistant, or by the person.

    Kept to learn which tools assistants use and what they ask for that no
    tool serves, so Reef can be improved. Strictly per person, like the
    search log: a person sees their own rows and nobody
    else's, operators included. See :func:`reef.rls.call_log_statements`.

    ``reason`` is the assistant's own words for why it called, cut to
    :data:`reef.rls.CALL_LOG_MAX_REASON` characters. It never reaches Logfire
    or the app logs. ``writer`` is ``assistant``, ``person`` or ``system``.
    Rows older than :data:`reef.rls.CALL_LOG_DAYS` days are deleted.
    """

    id = UUID(primary_key=True, default=uuid4)
    # NOT NULL in the migrated schema; see Space.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    session_id = Varchar(null=True, default=None)
    method = Varchar()
    reason = Varchar(length=None, default="")
    writer = Varchar()
    duration_ms = Integer(default=0)
    returned = Integer(default=0)
    outcome = Varchar()
    called_at = Timestamp(default=TimestampNow())


class Vector(Column):
    """A pgvector ``vector`` column of any length.

    Only here so that Piccolo creates the column. Values are written and
    compared in raw SQL, as text cast to ``vector``: see
    :mod:`reef.embedding`.
    """

    value_type = str


class EngramEmbedding(Table, tablename="engram_embeddings", db=DB):
    """The vector of one piece of one engram, from one model.

    An engram is cut into pieces by :func:`reef.sections.split`. Each piece is
    embedded, and the vector is stored here. Sections have no rows of their
    own: this is the only place they exist.

    ``section_anchor`` names the piece within the engram, and is null for the
    engram as a whole. ``content_hash`` is the digest of exactly the text that
    was embedded, so an unchanged piece is never embedded again. ``model_id``
    and ``dims`` say which model made the vector: vectors of two models
    cannot be compared, and a query only ever reads its own model's rows.

    Derived data. It is rebuilt from the engrams and never restored. A piece
    is visible exactly when its engram is: see
    :func:`reef.rls.embedding_statements`.
    """

    id = UUID(primary_key=True, default=uuid4)
    # NOT NULL in the migrated schema; see Space.owner_person_id.
    engram_id = ForeignKey(Engram, null=False)
    section_anchor = Varchar(length=None, null=True, default=None)
    model_id = Varchar()
    dims = Integer()
    content_hash = Varchar()
    embedding = Vector()
    created_at = Timestamp(default=TimestampNow())


class EmbeddingSpend(Table, tablename="embedding_spend", db=DB):
    """How many pieces were embedded for one person on one day.

    What the daily cap counts. It cannot be counted from
    ``engram_embeddings``: an engram rewritten a thousand times leaves a handful
    of rows there, and that rewriting is exactly what the cap is for.

    ``(person_id, day)`` is unique, by a raw constraint. Private to the
    person, like their jobs.
    """

    id = UUID(primary_key=True, default=uuid4)
    # NOT NULL in the migrated schema; see Space.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    day = Date()
    pieces = Integer(default=0)


class FilePage(Table, tablename="file_pages", db=DB):
    """The text of one page of one stored file.

    A page is a PDF page, an image or one frame of a TIFF, a slide or a
    sheet. A text format has no pages of its own: its text is cut between
    paragraphs into parts, and each part is a page. ``number`` counts from 1,
    and ``(attachment_id, number)`` is unique, by a raw constraint.

    ``reader`` names what read it, one of :data:`reef.rls.READERS`.
    ``model`` names the reader's model, where it has one, and ``confidence``
    is the reader's own, 0 to 100, where it gives one. A page with no words
    keeps empty ``text``: it was read and will not be read again.

    A generated ``search_vector`` column, which Piccolo cannot declare, is
    added by :func:`reef.search.search_statements`. A page is visible exactly
    when its file is: see :func:`reef.rls.file_text_statements`.
    """

    id = UUID(primary_key=True, default=uuid4)
    # NOT NULL in the migrated schema; see Space.owner_person_id.
    attachment_id = ForeignKey(Attachment, null=False)
    number = Integer()
    text = Varchar(length=None, default="")
    reader = Varchar()
    model = Varchar(null=True, default=None)
    confidence = Real(null=True, default=None)
    read_at = Timestamp(default=TimestampNow())


class FileEmbedding(Table, tablename="file_embeddings", db=DB):
    """The vector of one chunk of one stored file, from one model.

    Built as :class:`EngramEmbedding` is. ``chunk_anchor`` is null for the
    whole-file chunk: filename, description and the start of the text. Every
    file has that one, so a file without text is still found by meaning.

    Derived data, rebuilt from the file and never restored. A vector is
    visible exactly when its file is: see
    :func:`reef.rls.file_text_statements`.
    """

    id = UUID(primary_key=True, default=uuid4)
    # NOT NULL in the migrated schema; see Space.owner_person_id.
    attachment_id = ForeignKey(Attachment, null=False)
    chunk_anchor = Varchar(length=None, null=True, default=None)
    model_id = Varchar()
    dims = Integer()
    content_hash = Varchar()
    embedding = Vector()
    created_at = Timestamp(default=TimestampNow())


class ReadingSpend(Table, tablename="reading_spend", db=DB):
    """How much one reader read for one person on one day.

    What the daily page allowance counts. Token counts are for readers that
    charge by the token; a local reader leaves them at 0.

    ``(person_id, day, reader)`` is unique, by a raw constraint. Private to
    the person, like :class:`EmbeddingSpend`.
    """

    id = UUID(primary_key=True, default=uuid4)
    # NOT NULL in the migrated schema; see Space.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    day = Date()
    reader = Varchar()
    pages = Integer(default=0)
    input_tokens = Integer(default=0)
    output_tokens = Integer(default=0)


class EngramHeat(Table, tablename="engram_heat", db=DB):
    """How readily one engram comes to mind for one person: its activation.

    Derived from the usage log and rebuilt from it, so nothing here is a
    source of truth. Per person, like the log it is computed from: the same
    engram has a different activation for each member of a shared space.

    ``(person_id, engram_id)`` is unique, by a raw constraint.
    """

    id = UUID(primary_key=True, default=uuid4)
    # Both NOT NULL in the migrated schema; see Space.owner_person_id.
    person_id = ForeignKey(Person, null=False)
    engram_id = ForeignKey(Engram, null=False)
    activation = Real()
    accesses = Integer()
    last_access = Timestamp()
    updated_at = Timestamp(default=TimestampNow())


class TestAccount(Table, tablename="test_accounts", db=DB):
    """A made-up account the operator has flagged for scripted conversations.

    A flagged person may reset their memory to a fixed state and hurry their
    queued jobs, through two functions, and their calls are left out of the
    call log's totals. Only the operator flags: a person reads their own row
    and nothing else, and no policy lets anyone add, change or remove one. See
    :func:`reef.rls.test_account_statements`.

    ``reset_at`` is when the account was last reset, or ``None``.
    """

    __test__ = False  # Not a pytest class, whatever its name says.

    person_id = ForeignKey(Person, primary_key=True, on_delete=OnDelete.cascade)
    flagged_at = Timestamptz(default=TimestamptzNow())
    reset_at = Timestamptz(null=True, default=None)


TABLES = [
    Person,
    Space,
    Membership,
    Engram,
    Revision,
    Attachment,
    Promotion,
    SpaceAppearance,
    Job,
    Link,
    EngramAccess,
    EngramHeat,
    SearchLog,
    CallLog,
    EngramEmbedding,
    EmbeddingSpend,
    FilePage,
    FileEmbedding,
    ReadingSpend,
    TestAccount,
]
