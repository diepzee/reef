"""The tools that read inside files: ``recall``, ``open``, ``fetch``,
``store_file`` and ``forget``.

Pages are stored directly, as the worker would leave them. These tests manage
their own transactions, as the methods do behind the tools.
"""

import base64
import json
from uuid import UUID

import pytest
from conftest import Store, attach_file, caller_for

from reef import filetext
from reef.access import arm
from reef.attachments import get_attachment
from reef.config import get_settings
from reef.db import transaction_scope
from reef.methods import capture, connector, find, maintain
from reef.models import Job


@pytest.fixture(autouse=True)
def secret(monkeypatch) -> None:
    """Give staged tokens a signing secret long enough to use.

    :param monkeypatch: pytest's monkeypatch fixture
    """
    monkeypatch.setattr(get_settings().web, "session_secret", "m" * 40)


@pytest.fixture
def store(monkeypatch) -> Store:
    """Stand in for the object store wherever a file is stored or opened.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the stand-in
    """
    fake = Store()
    for module in (find, capture, maintain):
        monkeypatch.setattr(module, "S3ObjectStore", lambda: fake)
    return fake


@pytest.fixture
def reading(monkeypatch):
    """Switch reading on: these tests are about the text inside files.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the reading settings
    """
    settings = get_settings().reading
    monkeypatch.setattr(settings, "enabled", True)
    return settings


async def _stored(me, key: str, texts: list[str], space: str = "personal") -> UUID:
    """Give a stored file its pages, numbered from 1, and mark it read.

    :param me: the caller who owns the file
    :param key: the file's key
    :param texts: the text of each page
    :param space: the caller's name for the file's space
    :returns: the file's id
    """
    async with transaction_scope():
        await arm(me.principal)
        attachment = await get_attachment(me.principal, space, key)
        assert attachment is not None
        for number, text in enumerate(texts, 1):
            await Job.raw(
                "INSERT INTO file_pages (id, attachment_id, number, text, reader) "
                "VALUES (gen_random_uuid(), {}, {}, {}, 'tesseract')",
                attachment.id,
                number,
                text,
            )
        await Job.raw(
            "UPDATE attachments SET text_status = 'read', pages = {} WHERE id = {}",
            len(texts),
            attachment.id,
        )
        return attachment.id


async def _file(me, texts: list[str], description: str = "A policy.") -> str:
    """Store a file with pages and return its ref.

    :param me: who stores it
    :param texts: the text of each page
    :param description: what the file is
    :returns: the ref :func:`reef.methods.find.open` takes
    """
    key = await attach_file(me, "personal", description)
    await _stored(me, key, texts)
    return f"personal/file/{key}"


# --- recall ----------------------------------------------------------------------


async def test_recall_returns_a_passage_and_page_from_inside_a_file(
    household, store, reading
):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["Dear neighbours.", "The xylophone recital is at eight."])
    [hit] = await find.recall(me, "", "xylophone")
    assert hit["kind"] == "file"
    assert hit["page"] == 2
    assert "xylophone" in hit["passage"].lower()
    assert hit["ref"] == f"{ref}#page=2"
    # The ref is what open takes.
    [opened] = await find.open(me, "", [hit["ref"]])
    assert "error" not in opened


async def test_recall_file_hits_are_as_today_with_the_switch_off(household, store):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["The xylophone recital is at eight."])
    [hit] = await find.recall(me, "", "policy")
    assert set(hit) == {
        "kind",
        "space",
        "engram",
        "title",
        "passage",
        "author",
        "updated_at",
        "ref",
    }
    assert hit["ref"] == ref
    assert await find.recall(me, "", "xylophone") == []


# --- open ------------------------------------------------------------------------


async def test_open_returns_the_text_with_every_page_marked(household, store, reading):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["First page words.", "Second page words."])
    [file] = await find.open(me, "", [ref])
    assert file["text_status"] == "read"
    assert file["pages"] == 2
    assert file["read_by"] == ["Reef"]
    assert file["text"] == (
        "--- page 1 ---\nFirst page words.\n\n--- page 2 ---\nSecond page words."
    )
    assert file["text_cut_after_page"] is None
    assert file["filename"] == "policy.pdf"


async def test_open_cuts_a_long_file_at_a_page(household, store, reading, monkeypatch):
    monkeypatch.setattr(get_settings().engrams, "max_chars", 1_000)
    me = caller_for(household["wouter"])
    ref = await _file(me, [f"{n}" * 400 for n in "1234"])
    [file] = await find.open(me, "", [ref])
    assert file["text_cut_after_page"] == 2
    assert "--- page 2 ---" in file["text"]
    assert "--- page 3 ---" not in file["text"]
    assert len(file["text"]) <= 1_000


async def test_open_reads_a_long_file_from_the_anchored_page(
    household, store, reading, monkeypatch
):
    monkeypatch.setattr(get_settings().engrams, "max_chars", 1_000)
    me = caller_for(household["wouter"])
    ref = await _file(me, [f"{n}" * 400 for n in "1234"])
    [file] = await find.open(me, "", [f"{ref}#page=3"])
    assert file["text"].startswith("--- page 3 ---")
    # Pages 3 and 4 fit: nothing is left out after them.
    assert file["text_cut_after_page"] is None
    assert file["ref"] == f"{ref}#page=3"


async def test_an_anchor_on_a_file_that_fits_returns_it_whole(
    household, store, reading
):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["One.", "Two.", "Three."])
    [file] = await find.open(me, "", [f"{ref}#page=3"])
    assert file["text"].startswith("--- page 1 ---")
    assert file["text_cut_after_page"] is None


@pytest.mark.parametrize("anchor", ["#page=0", "#page=x", "#page=", "#p=2", "#"])
async def test_a_malformed_anchor_is_not_found(household, store, reading, anchor):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["One."])
    [file] = await find.open(me, "", [f"{ref}{anchor}"])
    assert file == {"ref": f"{ref}{anchor}", "error": "not_found"}


async def test_an_anchor_past_the_last_page_reads_the_last_page(
    household, store, reading, monkeypatch
):
    monkeypatch.setattr(get_settings().engrams, "max_chars", 1_000)
    me = caller_for(household["wouter"])
    ref = await _file(me, [f"{n}" * 400 for n in "1234"])
    [file] = await find.open(me, "", [f"{ref}#page=99"])
    assert file["text"].startswith("--- page 4 ---")
    assert file["text_cut_after_page"] is None


async def test_open_on_a_file_is_as_today_with_the_switch_off(household, store):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["Hidden words."])
    [file] = await find.open(me, "", [ref])
    assert set(file) == {
        "ref",
        "kind",
        "space",
        "key",
        "filename",
        "mime",
        "size",
        "description",
        "url",
        "expires_in",
    }


async def test_a_file_the_caller_cannot_open_gives_no_text(household, store, reading):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["Private words."])
    stranger = caller_for(household["partner"])
    [file] = await find.open(stranger, "", [ref])
    assert file == {"ref": ref, "error": "not_found"}


# --- fetch -----------------------------------------------------------------------


async def test_fetch_returns_description_and_text(household, store, reading):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["First.", "Second."], description="The policy.")
    key = ref.removeprefix("personal/file/")
    fetched = await connector.fetch(me, "", f"file:personal/{key}")
    assert fetched["text"] == (
        "The policy.\n\n--- page 1 ---\nFirst.\n\n--- page 2 ---\nSecond."
    )


async def test_fetch_cuts_at_the_cap_minus_the_description(
    household, store, reading, monkeypatch
):
    monkeypatch.setattr(get_settings().engrams, "max_chars", 1_000)
    me = caller_for(household["wouter"])
    ref = await _file(me, ["a" * 400, "b" * 400, "c" * 400], description="D" * 100)
    key = ref.removeprefix("personal/file/")
    fetched = await connector.fetch(me, "", f"file:personal/{key}")
    assert len(fetched["text"]) <= 1_000
    assert "--- page 2 ---" in fetched["text"]
    assert "--- page 3 ---" not in fetched["text"]


async def test_fetch_is_as_today_with_the_switch_off(household, store):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["Hidden."], description="The policy.")
    key = ref.removeprefix("personal/file/")
    fetched = await connector.fetch(me, "", f"file:personal/{key}")
    assert fetched["text"] == "The policy."


# --- store_file ------------------------------------------------------------------


async def _store(me, filename: str, data: bytes) -> dict:
    """Store a file through the method.

    :param me: who stores it
    :param filename: the file's name
    :param data: its bytes
    :returns: the method's answer
    """
    return await capture.store_file(
        me,
        "",
        "personal",
        filename,
        base64.b64encode(data).decode(),
        "application/octet-stream",
        "A file.",
    )


async def test_store_file_says_whether_reef_will_read_it(
    household, store, reading, monkeypatch
):
    me = caller_for(household["wouter"])
    assert (await _store(me, "a.pdf", b"%PDF-1.4"))["text"] == "waiting"
    assert (await _store(me, "b.psd", b"8BPS\x00\x01"))["text"] == "not_read"
    monkeypatch.setattr(reading, "enabled", False)
    assert "text" not in await _store(me, "c.pdf", b"%PDF-1.4")


# --- forget ----------------------------------------------------------------------


async def test_forget_says_the_text_goes_too(household, store, reading):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["Some words."])
    key = ref.removeprefix("personal/file/")
    staged = await maintain.forget(me, "", "personal", file=key)
    assert staged["effect"] == (
        'This permanently deletes the file "policy.pdf" (4 bytes) and the text '
        "Reef read from it. It cannot be undone."
    )


async def test_forget_a_file_without_pages_does_not_mention_text(
    household, store, reading
):
    me = caller_for(household["wouter"])
    key = await attach_file(me, "personal", "A policy.")
    staged = await maintain.forget(me, "", "personal", file=key)
    assert "text" not in staged["effect"]


async def test_forget_is_as_today_with_the_switch_off(household, store):
    me = caller_for(household["wouter"])
    ref = await _file(me, ["Some words."])
    key = ref.removeprefix("personal/file/")
    staged = await maintain.forget(me, "", "personal", file=key)
    assert staged["effect"] == (
        'This permanently deletes the file "policy.pdf" (4 bytes). It cannot be undone.'
    )


# --- load_index ------------------------------------------------------------------


async def test_load_index_is_the_same_size_with_the_switch_on(
    household, store, monkeypatch
):
    me = caller_for(household["wouter"])
    await _file(me, ["One.", "Two."])
    await _file(me, ["Three."])
    off = await find.load_index(me, "")
    monkeypatch.setattr(get_settings().reading, "enabled", True)
    on = await find.load_index(me, "")
    assert json.dumps(on) == json.dumps(off)


# --- the pieces ------------------------------------------------------------------


def test_render_text_marks_pages_and_cuts_at_a_page():
    pages = [filetext.StoredPage(n, "x" * 10, "tesseract") for n in (1, 2, 3)]
    whole, cut = filetext.render_text(pages)
    assert whole.count("--- page") == 3 and cut is None
    text, cut = filetext.render_text(pages, cap=55)
    assert cut == 2 and text.count("--- page") == 2
    assert filetext.render_text(pages, start=3, cap=55)[0].startswith("--- page 3")
    five = [filetext.StoredPage(n, "x" * 10, "tesseract") for n in range(1, 6)]
    assert filetext.render_text(five, start=2, cap=55)[1] == 3
    assert filetext.render_text(pages, start=2, cap=1_000)[0].startswith("--- page 1")


def test_read_by_names_the_readers_of_pages_with_text():
    pages = [
        filetext.StoredPage(1, "", "tesseract"),
        filetext.StoredPage(2, "words", "tesseract"),
    ]
    assert filetext.read_by(pages) == ["Reef"]
    assert filetext.read_by(pages[:1]) == []


def test_a_ref_ends_in_a_page_only():
    assert find._parse_ref("personal/file/k#page=3") == find.Ref(
        "personal", "file", "k", 3
    )
    assert find._parse_ref("personal/file/k") == find.Ref("personal", "file", "k")
    assert find._parse_ref("personal/file/k#page=0") is None
    assert find._parse_ref("personal/notes/car.md") == find.Ref(
        "personal", "engram", "notes/car.md"
    )
