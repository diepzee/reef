import json
from io import BytesIO
from zipfile import ZipFile

from reef.access import Principal
from reef.engrams import get_engram, save_engram
from reef.export import (
    build_full_dump,
    build_json_export,
    build_markdown_archive,
    export_space,
    render_engram,
)
from reef.filetext import StoredPage, read_by
from reef.models import Attachment, AttachmentStatus, Engram


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_render_round_trips_through_the_import_parser(tx, household):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("scripts")))
    from import_mark import parse_markdown

    me = principal_for(household["wouter"])
    await save_engram(
        me, "household", "house.md", "boiler", message="x", title="House", tags=["home"]
    )
    meta, body = parse_markdown(
        render_engram(await get_engram(me, "household", "house.md"))
    )
    assert meta["title"] == "House" and meta["tags"] == ["home"]
    assert body.strip() == "boiler"


async def test_export_writes_one_file_per_engram(tx, household, tmp_path):
    me = principal_for(household["wouter"])
    await save_engram(me, "household", "house.md", "boiler", message="x")
    await save_engram(me, "household", "money.md", "loan", message="x")
    assert await export_space(me, "household", tmp_path) == 2
    assert "loan" in (tmp_path / "money.md").read_text()


async def test_json_export_can_scope_one_space(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "private.md", "mine", message="x")
    await save_engram(me, "household", "shared.md", "ours", message="x")

    payload = json.loads(await build_json_export(me, "personal"))

    assert [space["alias"] for space in payload["spaces"]] == ["personal"]
    assert payload["spaces"][0]["engrams"][0]["body"] == "mine"


async def test_export_schema_2(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "household", "shared.md", "ours", message="x")

    payload = json.loads(await build_json_export(me))

    assert payload["schema_version"] == 2
    assert "spaces" in payload and "engrams" in payload["spaces"][0]
    assert "coves" not in payload and "pages" not in payload["spaces"][0]


async def test_markdown_archive_neutralizes_engram_path_traversal(tx, household):
    """A traversal path already in the store must not escape the archive.

    ``save_engram`` refuses this path now, so the row is written directly --
    which is the case that matters. Paths were unconstrained before
    ``normalize_path`` existed, so a corpus imported back then can still hold
    one, and the export is the last thing standing between it and the
    reader's filesystem.
    """
    me = principal_for(household["wouter"])
    saved = await save_engram(
        me, "personal", "escape.md", "still contained", message="x"
    )
    await Engram.update({Engram.path: "../escape.md"}).where(Engram.id == saved.id)

    with ZipFile(BytesIO(await build_markdown_archive(me, "personal"))) as archive:
        names = archive.namelist()
        assert "spaces/personal/engrams/escape.md" in names
        assert all(
            not name.startswith("/") and ".." not in name.split("/") for name in names
        )


class DumpStore:
    """Minimal object reader for a deterministic full-dump test."""

    async def get(self, key: str) -> bytes:
        assert key == "attachments/report-key"
        return b"quarterly report bytes"


async def test_full_dump_contains_history_files_and_access_context(tx, household):
    me = principal_for(household["wouter"])
    engram = await save_engram(
        me, "household", "report.md", "first version", message="create"
    )
    await save_engram(
        me,
        "household",
        "report.md",
        "second version",
        message="update",
        expected_version=1,
    )
    await Attachment(
        space_id=household["shared"].id,
        engram_id=engram.id,
        object_key="attachments/report-key",
        filename="quarterly report.pdf",
        mime="application/pdf",
        byte_size=len(b"quarterly report bytes"),
        description="The quarterly report.",
        status=AttachmentStatus.READY.value,
    ).save()

    with ZipFile(BytesIO(await build_full_dump(me, store=DumpStore()))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        revisions = json.loads(archive.read("data/revisions.json"))
        files = json.loads(archive.read("data/files.json"))
        assert manifest["contents"]["revisions"] == 2
        assert next(c for c in manifest["spaces"] if c["alias"] == "household")[
            "members"
        ] == ["Partner", "Wouter"]
        assert {revision["body"] for revision in revisions} == {
            "first version",
            "second version",
        }
        assert archive.read(files[0]["archive_path"]) == b"quarterly report bytes"
        assert "spaces/household/engrams/report.md" in archive.namelist()


async def _file_with_text(household, filename: str = "scan.pdf") -> Attachment:
    """Store a file read in two pages, the first by Tesseract.

    :param household: the household fixture
    :param filename: the stored filename
    :returns: the file
    """
    me = principal_for(household["wouter"])
    engram = await save_engram(me, "household", "r.md", "x", message="x")
    file = Attachment(
        space_id=household["shared"].id,
        engram_id=engram.id,
        object_key="attachments/scan-key",
        filename=filename,
        mime="application/pdf",
        byte_size=3,
        description="A scan.",
        status=AttachmentStatus.READY.value,
        text_status="read",
        pages=2,
    )
    await file.save()
    for number, text in ((1, "First page words."), (2, "Second page words.")):
        await Attachment.raw(
            "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
            "VALUES (gen_random_uuid(), {}, {}, {}, 'tesseract')",
            file.id,
            number,
            text,
        )
    return file


class ScanStore:
    """Object reader for the scan."""

    async def get(self, key: str) -> bytes:
        """Return the scan's bytes."""
        return b"%PDF"


async def test_the_full_dump_puts_each_files_text_beside_it(tx, household):
    """The text is whole, its pages marked, and the entry names it."""
    me = principal_for(household["wouter"])
    await _file_with_text(household, filename="../we/ird\\name.pdf")
    plain = await Attachment(
        space_id=household["shared"].id,
        object_key="attachments/plain-key",
        filename="plain.pdf",
        mime="application/pdf",
        byte_size=3,
        description="No text.",
        status=AttachmentStatus.READY.value,
    ).save()
    del plain

    with ZipFile(BytesIO(await build_full_dump(me, store=ScanStore()))) as archive:
        entries = {e["key"]: e for e in json.loads(archive.read("data/files.json"))}
        scan = entries["attachments/scan-key"]
        assert scan["text_status"] == "read"
        assert scan["pages"] == 2
        assert scan["readers"] == read_by([StoredPage(1, "x", "tesseract")])
        assert scan["text_path"] == f"{scan['archive_path']}.txt"
        assert scan["text_path"].startswith("spaces/household/files/scan-key/")
        assert scan["text_path"].count("/") == 4
        assert archive.read(scan["text_path"]).decode() == (
            "--- page 1 ---\nFirst page words.\n\n--- page 2 ---\nSecond page words."
        )
        none = entries["attachments/plain-key"]
        assert none["text_path"] is None and none["readers"] == []
        assert none["text_status"] is None
        assert not any(
            name.endswith(".txt") and "plain-key" in name for name in archive.namelist()
        )


async def test_smaller_exports_carry_the_status_only(tx, household):
    """The JSON export and the Markdown archive name the status, not the text."""
    me = principal_for(household["wouter"])
    await _file_with_text(household)

    payload = json.loads(await build_json_export(me, "household"))
    [entry] = payload["spaces"][0]["files"]
    assert entry["text_status"] == "read"
    assert not {"pages", "readers", "text_path"} & set(entry)

    with ZipFile(BytesIO(await build_markdown_archive(me, "household"))) as archive:
        [listed] = json.loads(archive.read("files.json"))
        assert listed["text_status"] == "read"
        assert not {"pages", "readers", "text_path"} & set(listed)
        assert not any(
            "page words" in archive.read(n).decode(errors="ignore")
            for n in archive.namelist()
        )


async def test_vectors_stay_out_of_the_dump(tx, household):
    """A file's vector is derived data: no part of the dump carries it."""
    me = principal_for(household["wouter"])
    file = await _file_with_text(household)
    await Attachment.raw(
        "INSERT INTO file_embeddings "
        "(id, attachment_id, model_id, dims, content_hash, embedding) "
        "VALUES (gen_random_uuid(), {}, 'fake-1', 3, 'x', '[1,2,3]'::vector)",
        file.id,
    )

    with ZipFile(BytesIO(await build_full_dump(me, store=ScanStore()))) as archive:
        for name in archive.namelist():
            body = archive.read(name).decode(errors="ignore")
            assert "[1,2,3]" not in body and "fake-1" not in body
