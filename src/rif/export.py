"""Portable exports and the complete per-principal data dump.

The original hand-run Markdown directory export remains available as
``python -m rif.export <alias> <target-dir>``. The web surface builds on the
same rendering with scoped Markdown/JSON downloads, plus a comprehensive ZIP
which includes revision history and stored file bytes.
"""

import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol
from zipfile import ZIP_DEFLATED, ZipFile

import yaml

from rif.access import AccessDenied, Principal, accessible_spaces, space_alias
from rif.attachments import S3ObjectStore
from rif.context import build_index
from rif.models import (
    Attachment,
    AttachmentStatus,
    Membership,
    Page,
    Person,
    Promotion,
    Revision,
)
from rif.pages import list_pages
from rif.spaces import display_names, member_names


class FileReader(Protocol):
    """The object-store capability a full dump needs."""

    async def get(self, key: str) -> bytes:
        """Return bytes stored under ``key``."""


def _json_default(value: object) -> str:
    """Serialize timestamps, dates, and UUID-like values for export JSON."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _json_bytes(payload: object) -> bytes:
    """Render stable, readable UTF-8 JSON."""
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n"
    ).encode()


def _safe_archive_path(value: str, *, fallback: str) -> str:
    """Turn an untrusted page path or filename into a ZIP-safe relative path.

    Page paths predate web export and are not syntactically constrained. ZIP
    entries must therefore remove absolute roots, traversal segments, control
    characters, and Windows separators before a user extracts the archive.

    :param value: stored path or filename
    :param fallback: used when no safe segment remains
    :returns: slash-separated relative archive path
    """
    parts = []
    for part in value.replace("\\", "/").split("/"):
        cleaned = "".join(char for char in part if ord(char) >= 32).strip()
        if cleaned in {"", ".", ".."}:
            continue
        parts.append(cleaned)
    return "/".join(parts) or fallback


async def _export_rows(
    principal: Principal, alias: str | None = None
) -> tuple[Person, list, list[Page], list[Attachment]]:
    """Load the current rows for one cove or every accessible cove."""
    # accessible_spaces arms the principal, so it must precede the person
    # lookup: read first and that query runs unarmed, returning nothing once
    # persons carries a policy, and the export would name nobody.
    spaces = await accessible_spaces(principal)
    person = await Person.objects().where(Person.id == principal.person_id).first()
    if alias is not None:
        spaces = [space for space in spaces if space_alias(space) == alias]
        if not spaces:
            raise AccessDenied(f"no space {alias!r} for {principal.email}")
    space_ids = [space.id for space in spaces]
    pages = (
        await Page.objects().where(Page.space_id.is_in(space_ids)).order_by(Page.path)
    )
    files = await Attachment.objects().where(
        Attachment.space_id.is_in(space_ids),
        Attachment.status == AttachmentStatus.READY.value,
    )
    return person, spaces, pages, files


def _file_metadata(file: Attachment, page_paths: dict[object, str]) -> dict:
    """Render portable metadata for one stored file."""
    return {
        "key": file.object_key,
        "filename": file.filename or file.object_key.rsplit("/", 1)[-1],
        "mime": file.mime,
        "size": file.byte_size,
        "description": file.description,
        "page_path": page_paths.get(file.page_id),
        "status": file.status,
        "created": file.created_at.isoformat(),
    }


def _page_payload(page: Page) -> dict:
    """Render one complete current page for JSON export."""
    return {
        "path": page.path,
        "title": page.title,
        "tags": list(page.tags),
        "body": page.body,
        "version": page.version,
        "created": page.created_at.isoformat(),
        "updated": page.updated_at.isoformat(),
    }


def render_page(page: Page) -> str:
    """Render a page as markdown with YAML frontmatter, import-compatible.

    :param page: the page to render
    :returns: markdown text
    """
    meta = {
        "title": page.title,
        "tags": list(page.tags),
        "updated": page.updated_at.date().isoformat(),
    }
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{page.body}\n"


async def export_space(principal: Principal, alias: str, target: Path) -> int:
    """Write every page in a space to a directory as markdown.

    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :param target: directory to write into
    :returns: number of files written
    """
    target.mkdir(parents=True, exist_ok=True)
    pages = await list_pages(principal, alias)
    for page in pages:
        destination = target / _safe_archive_path(page.path, fallback="untitled.md")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_page(page))
    return len(pages)


async def build_json_export(principal: Principal, alias: str | None = None) -> bytes:
    """Build a current-content JSON export for one cove or all coves.

    File metadata is included, while actual file bytes live in the full dump.

    :param principal: authenticated person
    :param alias: one cove alias, or ``None`` for every accessible cove
    :returns: UTF-8 JSON bytes
    """
    person, spaces, pages, files = await _export_rows(principal, alias)
    pages_by_space: dict[object, list[Page]] = {space.id: [] for space in spaces}
    files_by_space: dict[object, list[Attachment]] = {space.id: [] for space in spaces}
    for page in pages:
        pages_by_space[page.space_id].append(page)
    for file in files:
        files_by_space[file.space_id].append(file)
    page_paths = {page.id: page.path for page in pages}

    return _json_bytes(
        {
            "schema_version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "profile": {
                "email": person.email,
                "display_name": person.display_name,
            },
            "coves": [
                {
                    "alias": space_alias(space),
                    "version": space.version,
                    "pages": [_page_payload(page) for page in pages_by_space[space.id]],
                    "files": [
                        _file_metadata(file, page_paths)
                        for file in files_by_space[space.id]
                    ],
                }
                for space in spaces
            ],
        }
    )


async def build_markdown_archive(
    principal: Principal, alias: str | None = None
) -> bytes:
    """Build a ZIP of current pages as import-compatible Markdown.

    :param principal: authenticated person
    :param alias: one cove alias, or ``None`` for every accessible cove
    :returns: ZIP bytes
    """
    person, spaces, pages, files = await _export_rows(principal, alias)
    alias_by_space = {space.id: space_alias(space) for space in spaces}
    page_paths = {page.id: page.path for page in pages}
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            _json_bytes(
                {
                    "schema_version": 1,
                    "format": "reef-markdown-export",
                    "exported_at": datetime.now(UTC).isoformat(),
                    "profile": {
                        "email": person.email,
                        "display_name": person.display_name,
                    },
                    "coves": [space_alias(space) for space in spaces],
                    "note": "Current pages only. Use Dump my data for history and file bytes.",
                }
            ),
        )
        for page in pages:
            page_path = _safe_archive_path(page.path, fallback="untitled.md")
            archive.writestr(
                f"coves/{alias_by_space[page.space_id]}/pages/{page_path}",
                render_page(page),
            )
        if files:
            archive.writestr(
                "files.json",
                _json_bytes(
                    [
                        {
                            "cove": alias_by_space[file.space_id],
                            **_file_metadata(file, page_paths),
                        }
                        for file in files
                    ]
                ),
            )
    return output.getvalue()


async def build_full_dump(
    principal: Principal, *, store: FileReader | None = None
) -> bytes:
    """Build the complete data-portability ZIP visible to a principal.

    Includes current Markdown pages, the exact index, revision bodies and
    authors, stored file metadata and bytes, cove membership display names,
    and the principal's sharing audit rows. Missing object-store bytes are
    recorded explicitly in ``data/file-errors.json`` rather than omitted
    silently.

    :param principal: authenticated person
    :param store: injectable object reader; defaults to R2 when files exist
    :returns: complete ZIP bytes
    """
    person, spaces, pages, files = await _export_rows(principal)
    alias_by_space = {space.id: space_alias(space) for space in spaces}
    space_by_id = {space.id: space for space in spaces}
    page_by_id = {page.id: page for page in pages}
    page_paths = {page.id: page.path for page in pages}

    revisions = (
        await Revision.select(
            Revision.page_id,
            Revision.path,
            Revision.title,
            Revision.tags,
            Revision.body,
            Revision.message,
            Revision.author_id,
            Revision.created_at,
        )
        .where(Revision.page_id.is_in(list(page_by_id)))
        .order_by(Revision.created_at)
    )
    memberships = await Membership.objects().where(
        Membership.person_id == principal.person_id,
        Membership.space_id.is_in(list(space_by_id)),
    )
    role_by_space = {membership.space_id: membership.role for membership in memberships}
    shares = await Promotion.objects().where(Promotion.person_id == principal.person_id)
    index = await build_index(principal)

    cove_manifest = []
    for space in spaces:
        cove_manifest.append(
            {
                "alias": space_alias(space),
                "version": space.version,
                "you_are_owner": space.owner_person_id == principal.person_id,
                "your_role": role_by_space.get(space.id),
                "members": await member_names(space.id),
                "page_count": sum(page.space_id == space.id for page in pages),
                "file_count": sum(file.space_id == space.id for file in files),
            }
        )

    # Author names come from ``display_names`` rather than a join through
    # ``author_id.display_name``: that join reads ``persons`` directly, and a
    # co-author's row stops being readable once that table carries a policy,
    # which would quietly drop every other member's name from the archive.
    author_names = await display_names(
        [r["author_id"] for r in revisions if r["author_id"] is not None]
    )

    revision_payload = []
    for revision in revisions:
        page = page_by_id.get(revision["page_id"])
        if page is None:
            continue
        revision_payload.append(
            {
                "cove": alias_by_space[page.space_id],
                "path": revision["path"],
                "title": revision["title"],
                "tags": list(revision["tags"]),
                "body": revision["body"],
                "message": revision["message"],
                "author": author_names.get(revision["author_id"]),
                "created": revision["created_at"].isoformat(),
            }
        )

    share_payload = []
    for share in shares:
        source = page_by_id.get(share.source_page_id)
        destination = space_by_id.get(share.dest_space_id)
        share_payload.append(
            {
                "source_cove": alias_by_space.get(source.space_id) if source else None,
                "source_path": source.path if source else None,
                "source_version": share.source_version,
                "destination_cove": space_alias(destination) if destination else None,
                "destination_path": share.dest_path,
                "section_text": share.section_text,
                "created": share.created_at.isoformat(),
                "consumed": share.consumed_at.isoformat()
                if share.consumed_at
                else None,
            }
        )

    output = BytesIO()
    file_metadata = []
    file_errors = []
    if files and store is None:
        store = S3ObjectStore()

    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for page in pages:
            path = _safe_archive_path(page.path, fallback="untitled.md")
            archive.writestr(
                f"coves/{alias_by_space[page.space_id]}/pages/{path}",
                render_page(page),
            )

        for file in files:
            metadata = {
                "cove": alias_by_space[file.space_id],
                **_file_metadata(file, page_paths),
            }
            token = _safe_archive_path(
                file.object_key.rsplit("/", 1)[-1], fallback="stored-file"
            )
            filename = _safe_archive_path(
                metadata["filename"], fallback="download.bin"
            ).replace("/", "-")
            archive_path = f"coves/{metadata['cove']}/files/{token}/{filename}"
            metadata["archive_path"] = archive_path
            try:
                archive.writestr(archive_path, await store.get(file.object_key))
                metadata["included"] = True
            # A data dump should still download if R2 has one damaged object,
            # but the omission must be explicit in both metadata files.
            except Exception as error:  # noqa: BLE001
                metadata["included"] = False
                file_errors.append(
                    {
                        "cove": metadata["cove"],
                        "key": file.object_key,
                        "filename": metadata["filename"],
                        "error": type(error).__name__,
                    }
                )
            file_metadata.append(metadata)

        archive.writestr("index.json", _json_bytes(asdict(index)))
        archive.writestr("data/current.json", await build_json_export(principal))
        archive.writestr("data/revisions.json", _json_bytes(revision_payload))
        archive.writestr("data/files.json", _json_bytes(file_metadata))
        archive.writestr("data/shares.json", _json_bytes(share_payload))
        archive.writestr("data/file-errors.json", _json_bytes(file_errors))
        archive.writestr(
            "manifest.json",
            _json_bytes(
                {
                    "schema_version": 1,
                    "format": "reef-full-data-dump",
                    "exported_at": datetime.now(UTC).isoformat(),
                    "profile": {
                        "email": person.email,
                        "display_name": person.display_name,
                    },
                    "coves": cove_manifest,
                    "contents": {
                        "current_pages": len(pages),
                        "revisions": len(revision_payload),
                        "files": len(file_metadata),
                        "file_errors": len(file_errors),
                        "sharing_audits": len(share_payload),
                    },
                }
            ),
        )
    return output.getvalue()


async def _main(alias: str, target: str) -> None:
    """CLI entrypoint: export one space as the dev principal.

    :param alias: ``personal`` or a shared-space slug
    :param target: output directory
    """
    import os

    from rif.db import DB, transaction_scope
    from rif.identity import person_by_email

    await DB.start_connection_pool()
    async with transaction_scope():
        # Pre-auth: no principal exists yet, so this goes through the narrow
        # definer lookup like every other identity-binding path.
        identity = await person_by_email(os.environ["RIF_DEV_PRINCIPAL_EMAIL"])
        if identity is None:
            raise SystemExit("RIF_DEV_PRINCIPAL_EMAIL names no known person")
        principal = Principal(person_id=identity.person_id, email=identity.email)
        count = await export_space(principal, alias, Path(target))
    print(f"exported {count} page(s) to {target}")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1], sys.argv[2]))
