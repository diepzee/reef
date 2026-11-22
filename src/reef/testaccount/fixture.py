"""The made-up person a test account is loaded with.

Everything in ``person/`` is invented: the name, the street, the employer and
the documents. The data ships inside the package, so a reset works from a
deployed build as well as from a checkout.

Layout of ``person/``:

* ``person.toml``: the fixture ``version`` and the made-up ``name``
* ``persona.md``: the persona every assistant follows
* ``engrams/``: one Markdown file per engram, its path relative to this
  folder, with TOML front matter (``title``, ``tags``) between ``+++`` lines
* ``files.toml`` and ``files/``: the stored files and what describes them
"""

import tomllib
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable

from reef.access import Principal
from reef.attachments import ObjectStore, add_attachment
from reef.db import transaction_scope
from reef.engrams import PERSONA_PATH, save_engram

RESET_MESSAGE = "test account reset"
"""The reason recorded on every revision a reset writes."""


@dataclass(frozen=True)
class FixtureEngram:
    """One engram of the made-up person.

    :param path: where it is saved in the personal space
    :param title: its title
    :param tags: its tags
    :param body: its Markdown text
    """

    path: str
    title: str
    tags: list[str]
    body: str


@dataclass(frozen=True)
class FixtureFile:
    """One stored file of the made-up person.

    :param filename: the file's name
    :param mime: its content type
    :param description: what the file is, as the person would say it
    :param engram: the engram it is attached to, if any
    :param data: its bytes
    """

    filename: str
    mime: str
    description: str
    engram: str | None
    data: bytes


@dataclass(frozen=True)
class Fixture:
    """The whole made-up person.

    :param version: bumps when the content changes, so a reset can tell
    :param persona: the persona text
    :param engrams: every engram
    :param files: every stored file
    """

    version: int
    persona: str
    engrams: list[FixtureEngram]
    files: list[FixtureFile]


def _split_front_matter(text: str) -> tuple[dict, str]:
    """Split a Markdown file into its TOML front matter and its body.

    :param text: the file's text, starting with a ``+++`` line
    :returns: the front matter as a dict, and the body
    :raises ValueError: when the front matter is missing or unclosed
    """
    head, sep, rest = text.partition("+++\n")
    if head or not sep:
        raise ValueError("missing +++ front matter")
    meta, sep, body = rest.partition("\n+++\n")
    if not sep:
        raise ValueError("unclosed +++ front matter")
    return tomllib.loads(meta), body.lstrip("\n")


def _engrams(folder: Traversable, prefix: str = "") -> list[FixtureEngram]:
    """Read every engram under a folder, in path order.

    :param folder: the folder to walk
    :param prefix: the path of the folder inside the personal space
    :returns: the engrams
    """
    found: list[FixtureEngram] = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        if entry.is_dir():
            found += _engrams(entry, f"{prefix}{entry.name}/")
        elif entry.name.endswith(".md"):
            meta, body = _split_front_matter(entry.read_text(encoding="utf-8"))
            found.append(
                FixtureEngram(
                    path=f"{prefix}{entry.name}",
                    title=meta["title"],
                    tags=list(meta.get("tags", [])),
                    body=body,
                )
            )
    return found


def read_fixture() -> Fixture:
    """Read the made-up person from the package data.

    :returns: the person
    """
    root = resources.files("reef.testaccount") / "person"
    info = tomllib.loads((root / "person.toml").read_text(encoding="utf-8"))
    listing = tomllib.loads((root / "files.toml").read_text(encoding="utf-8"))
    files = [
        FixtureFile(
            filename=item["filename"],
            mime=item["mime"],
            description=item["description"],
            engram=item.get("engram"),
            data=(root / "files" / item["filename"]).read_bytes(),
        )
        for item in listing.get("file", [])
    ]
    return Fixture(
        version=info["version"],
        persona=(root / "persona.md").read_text(encoding="utf-8"),
        engrams=_engrams(root / "engrams"),
        files=files,
    )


async def load_fixture(
    principal: Principal, fixture: Fixture, store: ObjectStore | None
) -> tuple[int, int]:
    """Save the made-up person into a person's personal space.

    Each save and each file runs in its own transaction, as a tool call
    would. The writes are recorded as system writes and leave no usage rows.

    :param principal: the person to load, who must have a personal space
    :param fixture: what to load
    :param store: where file bytes go, or None to skip the files (local
        development has no object storage)
    :returns: how many engrams and how many files were loaded
    """
    async with transaction_scope():
        await save_engram(
            principal,
            "personal",
            PERSONA_PATH,
            fixture.persona,
            message=RESET_MESSAGE,
            allow_protected=True,
            channel="system",
            via=None,
        )
    for engram in fixture.engrams:
        async with transaction_scope():
            await save_engram(
                principal,
                "personal",
                engram.path,
                engram.body,
                message=RESET_MESSAGE,
                title=engram.title,
                tags=engram.tags,
                channel="system",
                via=None,
            )
    files = 0
    if store is not None:
        for item in fixture.files:
            await add_attachment(
                principal,
                "personal",
                item.data,
                item.mime,
                filename=item.filename,
                description=item.description,
                store=store,
                engram_path=item.engram,
            )
            files += 1
    return len(fixture.engrams), files
