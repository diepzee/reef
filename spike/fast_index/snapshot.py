"""Read one person's memory the way the fast-index variants need it.

Spike code. Everything runs as the person, through the app role, so row
security applies exactly as it does for ``load_index``.

A snapshot holds today's ``load_index`` payload (built by the real method),
one :class:`Entry` per engram with the signals a variant may rank by, and the
whole-engram vector of each engram that has one.

The private memory is personal data: nothing in this module prints.
"""

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts" / "eval"))

import evalkit

from reef import usage
from reef.access import Principal, accessible_spaces, alias_map
from reef.config import get_settings
from reef.db import transaction_scope
from reef.identity import person_by_email
from reef.methods import Caller
from reef.methods.find import load_index
from reef.models import Engram

#: Benchmark titles carry the session's date; it stands in for recency there.
_TITLE_DATE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")


@dataclass
class Entry:
    """One engram, with the signals a fast index may rank or group by."""

    space: str
    path: str
    title: str
    description: str
    tags: list[str]
    updated: datetime
    inbound: int
    files: int
    activation: float | None = None
    vector: np.ndarray | None = None

    @property
    def key(self) -> tuple[str, str]:
        """Return the engram's identity across spaces.

        :returns: space alias and path
        """
        return self.space, self.path

    @property
    def folder(self) -> str | None:
        """Return the engram's top-level folder, or None at the space root.

        :returns: the first path segment, or None
        """
        return self.path.split("/", 1)[0] if "/" in self.path else None


@dataclass
class Question:
    """A question and the engrams that answer it."""

    id: str
    text: str
    expected: list[tuple[str, str]]


@dataclass
class Snapshot:
    """Everything one measurement needs about one person's memory."""

    name: str
    principal: Principal
    index: dict
    entries: list[Entry]
    attachments: dict[str, int]
    questions: list[Question] = field(default_factory=list)

    @property
    def protocol(self) -> str:
        """Return the operating protocol and persona, as load_index sends them.

        :returns: the text
        """
        return self.index["operating_protocol"]


async def principal_for(email: str) -> Principal:
    """Find a local eval person.

    :param email: their address
    :returns: the person as a principal
    :raises SystemExit: if they were not seeded
    """
    identity = await person_by_email(email)
    if identity is None:
        sys.exit(f"no person {email}: seed this database first")
    return Principal(person_id=identity.person_id, email=identity.email)


def _parse_vector(text: str) -> np.ndarray:
    """Read a pgvector value written as text.

    :param text: ``[0.1,0.2,...]``
    :returns: the vector, normalised to length 1
    """
    vector = np.array(json.loads(text), dtype=np.float32)
    norm = float(np.linalg.norm(vector)) or 1.0
    return vector / norm


async def _vectors_and_activation(
    principal: Principal,
) -> tuple[dict[tuple[str, str], np.ndarray], dict[tuple[str, str], float]]:
    """Read whole-engram vectors and the person's activation, by engram.

    :param principal: the person
    :returns: vectors and activations, keyed by space alias and path
    """
    model = get_settings().embedding.model
    async with transaction_scope():
        spaces = await accessible_spaces(principal)
        aliases = await alias_map(principal)
        rows = await Engram.raw(
            "SELECT e.id, e.space_id, e.path, ee.embedding::text AS vector "
            "FROM engrams e LEFT JOIN engram_embeddings ee ON ee.engram_id = e.id "
            "AND ee.section_anchor IS NULL AND ee.model_id = {} "
            "WHERE e.space_id = ANY({})",
            model,
            [space.id for space in spaces],
        )
        heat = await usage.activations(principal)
    vectors = {}
    activation = {}
    for row in rows:
        key = (aliases[row["space_id"]], row["path"])
        if row["vector"]:
            vectors[key] = _parse_vector(row["vector"])
        if row["id"] in heat:
            activation[key] = heat[row["id"]]
    return vectors, activation


def _recency(name: str, title: str, updated: str) -> datetime:
    """Say when an engram was last current.

    In the benchmark every engram was written at seeding, so the stored date
    says nothing. The session date in its title stands in.

    :param name: the dataset's name
    :param title: the engram's title
    :param updated: the stored update time, ISO 8601
    :returns: the moment
    """
    if name.startswith("bench"):
        match = _TITLE_DATE.search(title or "")
        if match:
            return datetime(*(int(part) for part in match.groups()))
    return datetime.fromisoformat(updated).replace(tzinfo=None)


async def take(name: str, email: str) -> Snapshot:
    """Build a snapshot of one person's memory.

    :param name: ``private`` or ``bench<N>``, for recency and questions
    :param email: the person's address
    :returns: the snapshot
    """
    principal = await principal_for(email)
    caller = Caller(principal=principal, writer="assistant", session_id=None)
    index = await load_index(caller, "spike measurement")
    return await from_index(name, principal, index)


async def from_index(name: str, principal: Principal, index: dict) -> Snapshot:
    """Build a snapshot around an index payload that is already built.

    :param name: ``private`` or ``bench<N>``, for recency
    :param principal: the person
    :param index: today's ``load_index`` payload for them
    :returns: the snapshot
    """
    vectors, activation = await _vectors_and_activation(principal)
    entries: list[Entry] = []
    attachments: dict[str, int] = {}
    for space in index["spaces"]:
        per_engram: dict[str, int] = {}
        for attachment in space["attachments"]:
            owner = attachment["engram_path"]
            if owner:
                per_engram[owner] = per_engram.get(owner, 0) + 1
            else:
                attachments[space["alias"]] = attachments.get(space["alias"], 0) + 1
        for engram in space["engrams"]:
            key = (space["alias"], engram["path"])
            entries.append(
                Entry(
                    space=space["alias"],
                    path=engram["path"],
                    title=engram["title"] or "",
                    description=engram["description"] or "",
                    tags=list(engram["tags"]),
                    updated=_recency(name, engram["title"], engram["updated"]),
                    inbound=engram["inbound"],
                    files=per_engram.get(engram["path"], 0),
                    activation=activation.get(key),
                    vector=vectors.get(key),
                )
            )
    return Snapshot(
        name=name,
        principal=principal,
        index=index,
        entries=entries,
        attachments=attachments,
    )


def private_questions() -> list[Question]:
    """Read the private set's questions, with paths as the faithful seed stores them.

    The questions file names engrams as ``<alias>/<path>``; the faithful
    seed keeps the one space as ``personal`` with the original paths.

    :returns: the questions
    """
    raw = json.loads(evalkit.PRIVATE_QUESTIONS.read_text())
    questions = []
    for entry in raw:
        expected = []
        for ref in entry["expected"]:
            alias, _, path = ref.partition("/")
            expected.append(("personal" if alias == "personal" else alias, path))
        questions.append(Question(str(entry["id"]), entry["text"], expected))
    return questions


def bench_questions(count: int) -> list[Question]:
    """Read the benchmark questions a database was seeded with.

    :param count: the ``--questions`` the database was seeded with
    :returns: the questions, each expecting its evidence sessions
    """
    chosen = evalkit.convert(evalkit.choose(evalkit.load_benchmark(), count))
    return [
        Question(q.id, q.text, [("personal", path) for path in q.expected])
        for q in chosen.questions
    ]
