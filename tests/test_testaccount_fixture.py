"""The made-up person that test accounts are loaded with."""

import importlib.resources

from conftest import MemoryStore

from reef.access import Principal
from reef.db import transaction_scope
from reef.engrams import PERSONA_PATH, list_engrams, normalize_path, validate_body
from reef.models import Attachment, AttachmentStatus, EngramAccess
from reef.testaccount.fixture import load_fixture, read_fixture


def test_fixture_reads() -> None:
    """The package data reads into a persona, engrams and three files."""
    fixture = read_fixture()
    assert fixture.version >= 1
    assert fixture.persona.strip()
    assert 30 <= len(fixture.engrams) <= 40
    assert any(e.path.startswith("logs/") for e in fixture.engrams)
    mimes = {f.filename: f.mime for f in fixture.files}
    assert len(fixture.files) == 3
    assert sorted(mimes.values()).count("image/jpeg") == 1
    assert (
        list(mimes.values()).count("application/pdf")
        + list(mimes.values()).count("image/png")
        == 2
    )
    assert all(f.data and f.description for f in fixture.files)
    assert any(
        f.mime == "application/pdf" and f.data.startswith(b"%PDF")
        for f in fixture.files
    )


def test_contract_fixture_has_two_different_answers_without_a_relation() -> None:
    """The conversation must write the relation before it can rely on it."""
    fixture = read_fixture()
    engrams = {engram.path: engram for engram in fixture.engrams}
    old = engrams["work/contract-2026.md"]
    new = engrams["work/contract-2027.md"]
    assert "48 uur" in old.body
    assert "12 uur" in new.body
    assert "replaces:" not in old.body + new.body
    assert fixture.version >= 3


def test_fixture_paths_are_valid() -> None:
    """Every path is already normal and every body is acceptable."""
    fixture = read_fixture()
    paths = [e.path for e in fixture.engrams]
    assert len(set(paths)) == len(paths)
    for engram in fixture.engrams:
        assert normalize_path(engram.path) == engram.path
        validate_body(engram.path, engram.body)
        assert engram.title


def test_fixture_links_resolve() -> None:
    """Every same-space wiki link points at an engram in the fixture."""
    import re

    fixture = read_fixture()
    paths = {e.path for e in fixture.engrams}
    found = 0
    for engram in fixture.engrams:
        for target in re.findall(r"\[\[([^\]]+)\]\]", engram.body):
            found += 1
            assert target.split("#")[0] in paths, (engram.path, target)
    assert found >= 8


def test_fixture_is_shipped() -> None:
    """The data sits inside the package, next to the code."""
    root = importlib.resources.files("reef.testaccount") / "person"
    assert (root / "person.toml").is_file()


async def test_load_fixture_into_empty_person(graph) -> None:
    """Loading saves the persona, every engram and every file, and logs no use."""
    person = await graph.person("fixture@x.com", "Fixture")
    await graph.personal_space(person)
    principal = Principal(person_id=person.id, email=person.email)
    fixture = read_fixture()

    counts = await load_fixture(principal, fixture, MemoryStore())

    assert counts == (len(fixture.engrams), len(fixture.files))
    async with transaction_scope():
        saved = {e.path for e in await list_engrams(principal, "personal")}
        assert saved == {e.path for e in fixture.engrams} | {PERSONA_PATH}
        assert await EngramAccess.count() == 0
        ready = await Attachment.count().where(
            Attachment.status == AttachmentStatus.READY.value
        )
        assert ready == len(fixture.files)


async def test_load_fixture_without_storage_skips_files(graph) -> None:
    """With no object storage the engrams load and no file does."""
    person = await graph.person("nostore@x.com", "No Store")
    await graph.personal_space(person)
    principal = Principal(person_id=person.id, email=person.email)
    fixture = read_fixture()

    counts = await load_fixture(principal, fixture, None)

    assert counts == (len(fixture.engrams), 0)
    assert await Attachment.count() == 0
