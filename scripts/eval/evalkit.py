"""Measure how well search finds the right page, on a public benchmark.

The benchmark is LongMemEval-S (Wu et al., MIT licence): 500 questions, each
with the chat sessions that hold its answer and some forty that do not. Here
every session becomes an ordinary page in the personal cove of one dedicated
person, written through the same ``save_page`` an assistant uses, so a run
exercises the real write path, row security and search.

Two deliberate differences from the benchmark's own protocol:

- **One memory, not one haystack per question.** The sessions of all chosen
  questions are loaded together, because that is how reef is used. It is
  harder than the published setting, so these numbers are not comparable to
  published ones. They are comparable to each other, which is what a gate
  between two search modes needs.
- **Retrieval only.** A question counts as answered when a page holding its
  evidence is among the first results. No model reads the page.

Nothing here may touch a database that is not on this machine. See
:func:`refuse_unless_local`.
"""

import hashlib
import json
import os
import random
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg

from reef.access import Principal
from reef.coves import ensure_personal_cove
from reef.db import transaction_scope
from reef.identity import person_by_email
from reef.pages import list_pages, save_page
from reef.search import search_pages

#: The person every benchmark page belongs to. The address cannot receive
#: mail and names no real domain.
EVAL_EMAIL = "eval@reef.local"

#: Where the benchmark file comes from, and which revision of it.
SOURCE_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
    "resolve/main/longmemeval_s_cleaned.json"
)
SOURCE_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
ATTRIBUTION = (
    "LongMemEval (Wu et al., 2024), cleaned release, MIT licence. "
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned"
)

DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "longmemeval_s_cleaned.json"

#: Hosts that are this machine. Anything else is refused.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

#: How many results a search is asked for. Recall is judged on the first
#: ``k``; the reciprocal rank looks at all of them.
SEARCH_LIMIT = 10

_COVE = "personal"
_FOLDER = "sessions"
_BATCH = 50


class NotLocal(Exception):
    """Raised when a run would touch a database that is not on this machine."""


def refuse_unless_local(*dsns: str) -> None:
    """Stop unless every database named is on this machine.

    The benchmark must never be loaded into production: it would create a
    person nobody invited and thousands of pages of other people's chats.
    Railway sets ``RAILWAY_ENVIRONMENT`` in every service and in a
    ``railway run`` shell, so its presence is refused as well, whatever the
    address says.

    :param dsns: every connection string the run will use
    :raises NotLocal: if a host is not local, or a Railway environment is set
    """
    if any(name.startswith("RAILWAY_") for name in os.environ):
        raise NotLocal("a RAILWAY_ variable is set; the benchmark runs locally only")
    for dsn in dsns:
        host = urlsplit(dsn).hostname
        if host not in _LOCAL_HOSTS:
            raise NotLocal(
                f"the database host {host!r} is not this machine; "
                "the benchmark runs locally only"
            )


# --- the benchmark, as pages ---------------------------------------------------


@dataclass(frozen=True)
class EvalPage:
    """One benchmark session, as the page it becomes."""

    path: str
    title: str
    body: str


@dataclass(frozen=True)
class EvalQuestion:
    """One benchmark question and the pages that hold its answer."""

    id: str
    kind: str
    text: str
    answer: str
    expected: tuple[str, ...]


@dataclass(frozen=True)
class EvalSet:
    """Questions, and every page their sessions become."""

    questions: tuple[EvalQuestion, ...]
    pages: tuple[EvalPage, ...]
    source: str = ATTRIBUTION

    def to_json(self) -> str:
        """Serialise the set, for the small slice kept in the repository.

        :returns: the set as JSON text
        """
        return json.dumps(asdict(self), ensure_ascii=False, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "EvalSet":
        """Read a set written by :meth:`to_json`.

        :param text: JSON text
        :returns: the set
        """
        raw = json.loads(text)
        return cls(
            questions=tuple(
                EvalQuestion(**{**q, "expected": tuple(q["expected"])})
                for q in raw["questions"]
            ),
            pages=tuple(EvalPage(**p) for p in raw["pages"]),
            source=raw["source"],
        )


def _session_text(turns: list[dict]) -> str:
    """Render a chat session as markdown.

    :param turns: the session's turns, each with ``role`` and ``content``
    :returns: the page body
    """
    labels = {"user": "User", "assistant": "Assistant"}
    return "\n\n".join(
        f"**{labels.get(turn['role'], turn['role'].title())}:** {turn['content'].strip()}"
        for turn in turns
    )


def session_page(session_id: str, date: str, turns: list[dict]) -> EvalPage:
    """Turn one benchmark session into a page.

    The path carries a digest of the text. The benchmark reuses a session id
    across questions, sometimes with different text, and one memory cannot
    hold two pages at one path. With the digest, equal text shares a page and
    different text gets its own.

    The session's date goes in the title only. The benchmark gives the same
    session different dates under different questions, so a date cannot be
    stored as a fact about the page.

    :param session_id: the benchmark's id for the session
    :param date: the session's date as the benchmark writes it
    :param turns: the session's turns
    :returns: the page
    """
    body = _session_text(turns)
    digest = hashlib.sha256(body.encode()).hexdigest()[:8]
    slug = re.sub(r"[^a-z0-9]+", "-", session_id.lower()).strip("-")
    return EvalPage(
        path=f"{_FOLDER}/{slug}-{digest}.md",
        title=f"Conversation on {date.split(' ')[0]}",
        body=body,
    )


def is_abstention(record: dict) -> bool:
    """Say whether a benchmark question has no answer in its sessions.

    Such a question tests whether a model declines to answer. There is no
    page to find, so it says nothing about retrieval.

    :param record: one question as the benchmark file holds it
    :returns: True for an abstention question
    """
    return record["question_id"].endswith("_abs")


def choose(records: list[dict], count: int | None, seed: int = 0) -> list[dict]:
    """Pick questions, keeping each question type's share.

    The choice depends only on the question ids, the count and the seed, so
    two machines pick the same questions.

    :param records: the benchmark's questions
    :param count: how many to pick, or None for all of them
    :param seed: varies the choice
    :returns: the chosen questions, without abstention questions
    """
    answerable = sorted(
        (r for r in records if not is_abstention(r)), key=lambda r: r["question_id"]
    )
    if count is None or count >= len(answerable):
        return answerable
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for record in answerable:
        by_kind[record["question_type"]].append(record)
    chosen: list[dict] = []
    rng = random.Random(seed)
    for kind in sorted(by_kind):
        group = by_kind[kind]
        share = max(1, round(count * len(group) / len(answerable)))
        chosen.extend(rng.sample(group, min(share, len(group))))
    return sorted(chosen, key=lambda r: r["question_id"])[:count]


def convert(records: Iterable[dict], *, distractors: int | None = None) -> EvalSet:
    """Turn benchmark questions into pages and the paths each one expects.

    :param records: the questions to convert
    :param distractors: keep only this many sessions without evidence per
        question, the shortest first; None keeps them all. For building a
        small slice.
    :returns: the questions and the pages
    """
    pages: dict[str, EvalPage] = {}
    questions: list[EvalQuestion] = []
    for record in records:
        evidence = set(record["answer_session_ids"])
        sessions = list(
            zip(
                record["haystack_session_ids"],
                record["haystack_dates"],
                record["haystack_sessions"],
                strict=True,
            )
        )
        if distractors is not None:
            others = sorted(
                (s for s in sessions if s[0] not in evidence),
                key=lambda s: (sum(len(t["content"]) for t in s[2]), s[0]),
            )
            sessions = [s for s in sessions if s[0] in evidence] + others[:distractors]
        expected: list[str] = []
        for session_id, date, turns in sessions:
            page = session_page(session_id, date, turns)
            pages.setdefault(page.path, page)
            if session_id in evidence:
                expected.append(page.path)
        questions.append(
            EvalQuestion(
                id=record["question_id"],
                kind=record["question_type"],
                text=record["question"],
                answer=str(record["answer"]),
                expected=tuple(sorted(expected)),
            )
        )
    return EvalSet(
        questions=tuple(questions),
        pages=tuple(sorted(pages.values(), key=lambda p: p.path)),
    )


def load_benchmark(path: Path = DATA_FILE) -> list[dict]:
    """Read the downloaded benchmark file.

    :param path: where the file is
    :returns: its questions
    :raises FileNotFoundError: if it has not been downloaded
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Download it with: just eval-download"
        )
    return json.loads(path.read_text())


# --- seeding -------------------------------------------------------------------


async def ensure_eval_person(superuser_dsn: str) -> Principal:
    """Make sure the eval person and their personal cove exist.

    A person with no inviter cannot be created by the application: the
    policies demand one. So the person row is written out of band, as the
    superuser, exactly as reef's own first person was. Everything after that
    happens as the person, through the application.

    A local address is not proof of a local database: a tunnel to production
    listens on this machine too. So this also refuses a database in which
    anybody has signed in through the identity provider. A development
    database has no such people.

    :param superuser_dsn: a connection string for the local superuser
    :returns: the eval person, as a principal
    :raises NotLocal: if somebody has signed in to this database
    """
    connection = await asyncpg.connect(superuser_dsn)
    try:
        signed_in = await connection.fetchval(
            "SELECT count(*) FROM persons WHERE subject IS NOT NULL"
        )
        if signed_in:
            raise NotLocal(
                f"this database holds {signed_in} people who signed in; "
                "it is not a development database, so the benchmark stays out"
            )
        await connection.execute(
            "INSERT INTO persons (id, email, display_name) VALUES ($1, $2, $3) "
            "ON CONFLICT (email) DO NOTHING",
            uuid4(),
            EVAL_EMAIL,
            "Eval",
        )
    finally:
        await connection.close()
    identity = await person_by_email(EVAL_EMAIL)
    principal = Principal(person_id=identity.person_id, email=identity.email)
    async with transaction_scope():
        await ensure_personal_cove(principal.person_id, principal.email)
    return principal


async def seed_pages(
    principal: Principal,
    pages: Iterable[EvalPage],
    *,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Write the pages that are not there yet.

    Safe to run again: a path carries a digest of its text, so a page that
    exists already holds the right text and is skipped.

    A write is not recorded in the usage log. The eval person did not use
    these pages; a script loaded them.

    :param principal: the eval person
    :param pages: the pages to write
    :param progress: called with pages done and pages to do, after each batch
    :returns: how many pages were written
    """
    async with transaction_scope():
        present = {page.path for page in await list_pages(principal, _COVE)}
    missing = [page for page in pages if page.path not in present]
    for start in range(0, len(missing), _BATCH):
        async with transaction_scope():
            for page in missing[start : start + _BATCH]:
                await save_page(
                    principal,
                    _COVE,
                    page.path,
                    page.body,
                    message="benchmark seed",
                    title=page.title,
                    tags=["benchmark"],
                    via=None,
                )
        if progress is not None:
            progress(min(start + _BATCH, len(missing)), len(missing))
    return len(missing)


# --- search modes --------------------------------------------------------------

Searcher = Callable[[Principal, str], Awaitable[list[str]]]

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


async def _paths(principal: Principal, query: str) -> list[str]:
    """Search the eval person's pages and return the paths found, best first.

    :param principal: the eval person
    :param query: the search text
    :returns: page paths in rank order
    """
    async with transaction_scope():
        hits = await search_pages(principal, query, cove=_COVE, limit=SEARCH_LIMIT)
    return [hit["path"] for hit in hits if hit["kind"] == "page"]


async def lexical(principal: Principal, question: str) -> list[str]:
    """Search with the question exactly as asked.

    This is search as it ships. Since 18 November 2026 it matches pages
    holding any word of the query and ranks those holding every word first.
    Before that it required every word.

    :param principal: the eval person
    :param question: the question
    :returns: page paths in rank order
    """
    return await _paths(principal, question)


async def lexical_any(principal: Principal, question: str) -> list[str]:
    """Search for pages holding any word of the question, best match first.

    The shipped search, with the query rewritten as alternatives. It drops
    the "every word first" ordering, so the gap to :func:`lexical` shows
    what that ordering is worth.

    :param principal: the eval person
    :param question: the question
    :returns: page paths in rank order
    """
    words = list(dict.fromkeys(w.lower() for w in _WORD.findall(question)))
    return await _paths(principal, " or ".join(w for w in words if w != "or"))


#: Every way of searching the harness can measure, by name. Later modes
#: (semantic, hybrid) are added here.
MODES: dict[str, Searcher] = {"lexical": lexical, "lexical-any": lexical_any}


# --- scoring -------------------------------------------------------------------


@dataclass
class Tally:
    """Counts for one group of questions."""

    questions: int = 0
    found_any: int = 0
    found_all: int = 0
    reciprocal_ranks: float = 0.0

    def add(self, any_hit: bool, all_hit: bool, reciprocal_rank: float) -> None:
        """Count one question.

        :param any_hit: an expected page was in the first ``k`` results
        :param all_hit: every expected page was
        :param reciprocal_rank: one over the rank of the first expected page,
            or zero when none was returned
        """
        self.questions += 1
        self.found_any += any_hit
        self.found_all += all_hit
        self.reciprocal_ranks += reciprocal_rank

    def summary(self) -> dict[str, float | int]:
        """Return the rates for this group.

        :returns: question count, both recalls and the mean reciprocal rank
        """
        n = self.questions or 1
        return {
            "questions": self.questions,
            "recall_any": round(self.found_any / n, 4),
            "recall_all": round(self.found_all / n, 4),
            "mrr": round(self.reciprocal_ranks / n, 4),
        }


@dataclass
class Report:
    """How one search mode did on one set of questions."""

    mode: str
    k: int
    overall: Tally = field(default_factory=Tally)
    by_kind: dict[str, Tally] = field(default_factory=lambda: defaultdict(Tally))
    failures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return the report as plain data.

        :returns: mode, cutoff, overall and per-type rates, and the failures
        """
        return {
            "mode": self.mode,
            "k": self.k,
            "overall": self.overall.summary(),
            "by_kind": {kind: t.summary() for kind, t in sorted(self.by_kind.items())},
            "failures": self.failures,
        }


def judge(
    expected: Iterable[str], returned: list[str], k: int
) -> tuple[bool, bool, float]:
    """Judge one question's results.

    :param expected: the paths that hold the evidence
    :param returned: the paths search returned, best first
    :param k: how many leading results count for recall
    :returns: whether any expected path is in the first ``k``, whether all
        are, and the reciprocal rank of the first one anywhere in the results
    """
    wanted = set(expected)
    top = set(returned[:k])
    rank = next((i for i, path in enumerate(returned, 1) if path in wanted), None)
    return bool(wanted & top), wanted <= top, 1 / rank if rank else 0.0


def score(
    mode: str,
    questions: Iterable[EvalQuestion],
    results: dict[str, list[str]],
    k: int = 5,
) -> Report:
    """Score a mode's results over a set of questions.

    A failure is a question none of whose expected pages reached the first
    ``k`` results. The failure list names the question and the paths, never
    page text.

    :param mode: the mode's name, for the report
    :param questions: the questions asked
    :param results: returned paths per question id, best first
    :param k: how many leading results count for recall
    :returns: the report
    """
    report = Report(mode=mode, k=k)
    for question in questions:
        returned = results.get(question.id, [])
        any_hit, all_hit, reciprocal_rank = judge(question.expected, returned, k)
        report.overall.add(any_hit, all_hit, reciprocal_rank)
        report.by_kind[question.kind].add(any_hit, all_hit, reciprocal_rank)
        if not any_hit:
            report.failures.append(
                {
                    "id": question.id,
                    "kind": question.kind,
                    "question": question.text,
                    "expected": list(question.expected),
                    "returned": returned[:k],
                }
            )
    return report


async def run(
    principal: Principal,
    questions: Iterable[EvalQuestion],
    mode: str,
    k: int = 5,
) -> Report:
    """Ask every question in one mode and score the answers.

    :param principal: the eval person
    :param questions: the questions to ask
    :param mode: a key of :data:`MODES`
    :param k: how many leading results count for recall
    :returns: the report
    :raises KeyError: for a mode that does not exist
    """
    searcher = MODES[mode]
    questions = list(questions)
    results = {q.id: await searcher(principal, q.text) for q in questions}
    return score(mode, questions, results, k)
