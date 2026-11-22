"""Put a test account back to the made-up person, and say when it is ready.

A reset wipes everything in the spaces the person owns and loads the made-up
person of :mod:`reef.testaccount.fixture`, so each scripted conversation
starts from the same memory. It keeps the person, their sessions, their spaces and
memberships, and their search and call logs.

Two rules guard it, and the database checks both, past row security, in
``reef_test_account_reset()`` (see :func:`reef.rls.test_account_statements`):
the person must be flagged as a test account, and must share no space with
anyone. The check runs before anything is deleted, in the same transaction,
so a refusal changes nothing.

Everything runs as the armed person, under the same policies as their own
tool calls. The engrams' history, links, usage, activation and vectors go
with them by cascade, and a file's pages and vectors with the file.
"""

import logging
from dataclasses import dataclass

from asyncpg import InsufficientPrivilegeError

from reef import embedding
from reef.access import Principal, accessible_spaces, arm
from reef.attachments import ObjectStore, S3ObjectStore
from reef.config import get_settings
from reef.db import transaction_scope
from reef.jobs import (
    BACKFILL_EMBEDDINGS,
    BACKFILL_FILES,
    EMBED_ENGRAM,
    EMBED_FILE,
    READ_FILE,
)
from reef.models import Attachment, Engram, Job, Promotion, Revision, TestAccount
from reef.testaccount.fixture import load_fixture, read_fixture

logger = logging.getLogger(__name__)

_LOCK_CLASS = 0x7E57
"""The first key of the advisory lock a reset holds; the second is the person.

Two keys of 32 bits live apart from the single 64-bit keys the rest of Reef
locks on, so this lock cannot collide with those."""

_READINESS_KINDS = (
    EMBED_ENGRAM,
    BACKFILL_EMBEDDINGS,
    EMBED_FILE,
    READ_FILE,
    BACKFILL_FILES,
)
"""The jobs that bring search vectors and file text up to date."""


class NotATestAccount(Exception):
    """Raised when the person is not flagged as a test account."""


class SharedWithOthers(Exception):
    """Raised when the person shares a space with anyone."""


_REFUSALS: dict[str, type[Exception]] = {
    "not a test account": NotATestAccount,
    "shares a space with others": SharedWithOthers,
}
"""What ``reef_test_account_reset()`` says when it refuses, and what that means."""


@dataclass(frozen=True)
class ResetResult:
    """What a reset loaded, and which services this deployment has.

    :param fixture_version: the version of the made-up person loaded
    :param engrams: how many engrams were loaded, the persona not counted
    :param files: how many files were loaded
    :param services: whether each service is configured: ``embedding``,
        ``rerank``, ``file_text`` and ``files`` (object storage)
    """

    fixture_version: int
    engrams: int
    files: int
    services: dict[str, bool]


@dataclass(frozen=True)
class Readiness:
    """Whether the person's memory is ready to be searched as a whole.

    :param ready: True when nothing below is waiting
    :param vectors_missing: engrams and read files without a search vector,
        counted only when an embedder is configured
    :param files_unread: files still waiting to be read, counted only when
        file reading is on
    :param jobs_waiting: queued or running jobs that embed or read
    """

    ready: bool
    vectors_missing: int
    files_unread: int
    jobs_waiting: int


def configured_store() -> ObjectStore | None:
    """Return the object store, or None when this deployment has none.

    Local development has no object storage. A reset without one loads no
    files.

    :returns: the store when a bucket and its address are configured
    """
    files = get_settings().files
    if not (files.bucket and files.endpoint):
        return None
    return S3ObjectStore()


async def reset(principal: Principal, store: ObjectStore | None) -> ResetResult:
    """Wipe a test account and load the made-up person into it.

    One transaction, holding a lock on the person, so two resets at once run
    one after the other. In order: the database checks the flag and the
    sharing rule and clears the person's queued jobs and spend; everything in
    their spaces is deleted; the made-up person is loaded; the jobs loading
    queued are made due now. The bytes of the deleted files are erased only
    after the transaction commits.

    :param principal: the signed-in person
    :param store: where file bytes live, or None without object storage: file
        rows are then still deleted, but no bytes are erased and no files
        are loaded
    :raises NotATestAccount: when the person is not flagged
    :raises SharedWithOthers: when the person shares a space with anyone
    :returns: what was loaded, and which services are configured
    """
    fixture = read_fixture()
    async with transaction_scope():
        await TestAccount.raw(
            "SELECT pg_advisory_xact_lock({}, hashtext({}))",
            _LOCK_CLASS,
            str(principal.person_id),
        )
        await arm(principal)
        await _start(principal)
        keys = await _wipe(principal)
        # The loader's own transactions, the file store's two among them,
        # join this one: a reset that fails part-way loads nothing. Bytes it
        # already stored are then left with no row, the safer wreckage.
        engrams, files = await load_fixture(principal, fixture, store)
        await TestAccount.raw("SELECT reef_test_account_hurry()")
    if store is not None:
        await _erase(keys, store)
    return ResetResult(
        fixture_version=fixture.version,
        engrams=engrams,
        files=files,
        services=_services(store),
    )


async def _start(principal: Principal) -> None:
    """Check the person may be reset, and clear their queued jobs and spend.

    :param principal: the person, armed
    :raises NotATestAccount: when the person is not flagged
    :raises SharedWithOthers: when the person shares a space with anyone
    """
    try:
        await TestAccount.raw("SELECT reef_test_account_reset()")
    except InsufficientPrivilegeError as error:
        refusal = _REFUSALS.get(error.message)
        if refusal is None:
            raise
        raise refusal() from None


async def _wipe(principal: Principal) -> list[str]:
    """Delete everything in the spaces the person owns, as account deletion orders it.

    Only owned spaces, never every space the person can write to. The
    sharing check ran in an earlier statement, and under ``READ COMMITTED``
    a membership committed since then is visible here: another person may
    have admitted the test account to their space in between, and a member
    may delete every engram in a space.

    Revisions go before their engrams, and files are deleted explicitly, for
    the reason :func:`reef.account.delete_account_rows` gives. The person's
    own promotions go too, wherever their source is: a pending share must
    not survive a reset.

    :param principal: the person, armed
    :returns: the object keys of the deleted files, to erase after commit
    """
    await Promotion.delete().where(Promotion.person_id == principal.person_id)
    space_ids = [
        space.id
        for space in await accessible_spaces(principal)
        if space.owner_person_id == principal.person_id
    ]
    if not space_ids:
        return []
    keys = (
        await Attachment.select(Attachment.object_key)
        .where(Attachment.space_id.is_in(space_ids))
        .output(as_list=True)
    )
    engram_ids = (
        await Engram.select(Engram.id)
        .where(Engram.space_id.is_in(space_ids))
        .output(as_list=True)
    )
    if engram_ids:
        await Revision.delete().where(Revision.engram_id.is_in(engram_ids))
    await Attachment.delete().where(Attachment.space_id.is_in(space_ids))
    await Engram.delete().where(Engram.space_id.is_in(space_ids))
    return keys


async def _erase(keys: list[str], store: ObjectStore) -> None:
    """Erase the bytes of deleted files, logging only how many failed.

    The rows are gone and committed, so a failure leaves bytes nothing points
    at, which is the safer wreckage. The log carries a count and never a key
    or an error message, which could name one.

    :param keys: object keys to erase
    :param store: where the bytes live
    """
    failed = 0
    for key in keys:
        try:
            await store.delete(key)
        # Any failure is counted alike: the rows are gone either way.
        except Exception:  # noqa: BLE001
            failed += 1
    if failed:
        logger.warning(
            "test account reset: could not erase %d of %d stored files",
            failed,
            len(keys),
        )


def _services(store: ObjectStore | None) -> dict[str, bool]:
    """Say which services this deployment has configured.

    :param store: the object store the reset used, or None
    :returns: ``embedding``, ``rerank``, ``file_text`` and ``files``
    """
    settings = get_settings()
    return {
        "embedding": bool(settings.embedding.url),
        "rerank": bool(settings.rerank.url) and settings.rerank.enabled,
        "file_text": settings.reading.enabled,
        "files": store is not None,
    }


async def readiness(principal: Principal) -> Readiness:
    """Say whether search vectors and file text are ready for the person.

    Counts only what this deployment can produce: vectors when an embedder
    is configured, file text when reading is on. Jobs are counted either
    way, as a job still queued or running is work not yet done.

    :param principal: the signed-in person
    :returns: what is still missing, and whether that is nothing
    """
    settings = get_settings()
    async with transaction_scope():
        await arm(principal)
        vectors = 0
        if settings.embedding.url:
            vectors = len(await embedding.engrams_lacking(principal)) + len(
                await embedding.files_lacking(principal)
            )
        unread = 0
        if settings.reading.enabled:
            [row] = await Attachment.raw(
                "SELECT count(*)::int AS n FROM attachments WHERE status = 'ready' "
                "AND text_status = 'waiting' "
                "AND space_id IN (SELECT reef_member_space_ids())"
            )
            unread = row["n"]
        [row] = await Job.raw(
            "SELECT count(*)::int AS n FROM jobs WHERE person_id = {} "
            "AND status IN ('queued', 'running') AND kind = ANY({}::text[])",
            principal.person_id,
            list(_READINESS_KINDS),
        )
        jobs = row["n"]
    return Readiness(
        ready=vectors == unread == jobs == 0,
        vectors_missing=vectors,
        files_unread=unread,
        jobs_waiting=jobs,
    )
