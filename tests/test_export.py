import json
from io import BytesIO
from zipfile import ZipFile

from rif.access import Principal
from rif.export import (
    build_full_dump,
    build_json_export,
    build_markdown_archive,
    export_space,
    render_page,
)
from rif.models import Attachment, AttachmentStatus, Page
from rif.pages import get_page, save_page


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_render_round_trips_through_the_import_parser(tx, household):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("scripts")))
    from import_mark import parse_markdown

    me = principal_for(household["wouter"])
    await save_page(
        me, "household", "house.md", "boiler", message="x", title="House", tags=["home"]
    )
    meta, body = parse_markdown(
        render_page(await get_page(me, "household", "house.md"))
    )
    assert meta["title"] == "House" and meta["tags"] == ["home"]
    assert body.strip() == "boiler"


async def test_export_writes_one_file_per_page(tx, household, tmp_path):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "house.md", "boiler", message="x")
    await save_page(me, "household", "money.md", "loan", message="x")
    assert await export_space(me, "household", tmp_path) == 2
    assert "loan" in (tmp_path / "money.md").read_text()


async def test_json_export_can_scope_one_cove(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "private.md", "mine", message="x")
    await save_page(me, "household", "shared.md", "ours", message="x")

    payload = json.loads(await build_json_export(me, "personal"))

    assert [cove["alias"] for cove in payload["coves"]] == ["personal"]
    assert payload["coves"][0]["pages"][0]["body"] == "mine"


async def test_markdown_archive_neutralizes_page_path_traversal(tx, household):
    """A traversal path already in the store must not escape the archive.

    ``save_page`` refuses this path now, so the row is written directly --
    which is the case that matters. Paths were unconstrained before
    ``normalize_path`` existed, so a corpus imported back then can still hold
    one, and the export is the last thing standing between it and the
    reader's filesystem.
    """
    me = principal_for(household["wouter"])
    saved = await save_page(me, "personal", "escape.md", "still contained", message="x")
    await Page.update({Page.path: "../escape.md"}).where(Page.id == saved.id)

    with ZipFile(BytesIO(await build_markdown_archive(me, "personal"))) as archive:
        names = archive.namelist()
        assert "coves/personal/pages/escape.md" in names
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
    page = await save_page(
        me, "household", "report.md", "first version", message="create"
    )
    await save_page(
        me,
        "household",
        "report.md",
        "second version",
        message="update",
        expected_version=1,
    )
    await Attachment(
        space_id=household["shared"].id,
        page_id=page.id,
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
        assert next(c for c in manifest["coves"] if c["alias"] == "household")[
            "members"
        ] == ["Partner", "Wouter"]
        assert {revision["body"] for revision in revisions} == {
            "first version",
            "second version",
        }
        assert archive.read(files[0]["archive_path"]) == b"quarterly report bytes"
        assert "coves/household/pages/report.md" in archive.namelist()
