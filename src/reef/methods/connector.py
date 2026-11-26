"""The connector pair: ``search`` and ``fetch``.

Some clients call only these two tools. ChatGPT outside developer mode limits
a connector to them, and the same pair makes Reef usable as a company
knowledge source. Their schemas and results follow that contract, not the
rest of Reef: a search result is ``{id, title, url}``, a fetch is
``{id, title, text, url, metadata}``. They are kept exactly as they were
before the workflow methods, old words included (``page`` in ids and
metadata, ``cove`` in metadata), because a connector may hold ids from
before.

They take a ``reason`` as every method does, so they reach the call log,
but their tools pass an empty one: the contract has no place to ask for it.

**Access.** Both run as the caller and see what the caller could open
anyway. An id names a thing; it does not grant access to it. :func:`fetch`
resolves it again under the caller, so an id for something the caller may
not read gets the same ``not_found`` as one for nothing at all.
"""

from reef import filetext, usage
from reef.access import AccessDenied, Principal
from reef.attachments import get_attachment
from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import get_engram
from reef.links import replacement_refs
from reef.methods import Caller, method
from reef.models import AccessKind
from reef.protocol import build_instructions
from reef.search import search_engrams

#: The kinds of thing a connector id names, in the contract's old words.
_ID_KINDS = ("page", "file")

#: The id under which the operating protocol is offered as a document.
#:
#: A client limited to ``search`` and ``fetch`` never calls ``load_index``,
#: so the protocol would never reach it. Offered as an ordinary search hit,
#: first, it reaches a client that reads its top hit, in exactly the shape
#: the contract asks for.
PROTOCOL_ID = "protocol:operating-protocol"

#: Said to a model, not to a reader: this is the title in a result list, and
#: it is the whole of what persuades a connector to fetch it.
PROTOCOL_TITLE = "How to use this memory — read before answering"


def _connector_id(kind: str, space: str, locator: str) -> str:
    """Build the id :func:`search` hands out and :func:`fetch` takes back.

    :param kind: ``page`` or ``file``
    :param space: the caller's name for the space
    :param locator: the engram's path or the file's key
    :returns: the id
    """
    return f"{kind}:{space}/{locator}"


def _split_connector_id(identifier: str) -> tuple[str, str, str] | None:
    """Take an id apart, or return None if it is not one.

    Split on the first ``/`` only: engram paths contain slashes, space names
    do not.

    :param identifier: an id previously produced by :func:`_connector_id`
    :returns: kind, space and locator; None when the id is malformed
    """
    kind, _, remainder = identifier.partition(":")
    if kind not in _ID_KINDS or not remainder:
        return None
    space, separator, locator = remainder.partition("/")
    if not separator or not space or not locator:
        return None
    return kind, space, locator


def _app_url(space: str, path: str) -> str:
    """Return where a person reads this engram on the website.

    :param space: the caller's name for the space
    :param path: the engram's path; empty for the space itself
    :returns: the URL
    """
    base = get_settings().web.base_url.rstrip("/")
    return f"{base}/app/s/{space}/p/{path}"


def _docs_url() -> str:
    """Return where a person reads about Reef itself.

    :returns: the URL
    """
    base = get_settings().web.base_url.rstrip("/")
    return f"{base}/docs"


@method("search")
async def search(caller: Caller, reason: str, query: str) -> dict:
    """Search everything the caller can see, in the connector's shape.

    The first result is always the operating protocol: a connector call
    carries no memory of the last one, so there is no offering it once.

    :param caller: who is calling
    :param reason: why, in the caller's words; empty from the tool
    :param query: words to search for
    :returns: ``{"results": [...]}``, each with ``id``, ``title`` and ``url``
    """
    hits = await search_engrams(caller.principal, query, log=True)
    results = [{"id": PROTOCOL_ID, "title": PROTOCOL_TITLE, "url": _docs_url()}]
    for hit in hits:
        if hit["kind"] == "file":
            identifier = _connector_id("file", hit["cove"], hit["key"])
            title = hit["filename"]
            url = _app_url(hit["cove"], "")
        else:
            identifier = _connector_id("page", hit["cove"], hit["path"])
            title = hit["title"] or hit["path"]
            url = _app_url(hit["cove"], hit["path"])
        results.append(
            {
                "id": identifier,
                "title": title,
                "url": url,
                **(
                    {"replaced_by": hit["replaced_by"]}
                    if hit.get("replaced_by")
                    else {}
                ),
            }
        )
    return {"results": results}


@method("fetch")
async def fetch(caller: Caller, reason: str, id: str) -> dict:
    """Return one engram or file by an id :func:`search` handed out.

    :param caller: who is calling
    :param reason: why, in the caller's words; empty from the tool
    :param id: an id from :func:`search`
    :returns: ``id``, ``title``, ``text``, ``url`` and ``metadata``; or
        ``{"error": "not_found"}``, or ``{"error": "bad_id", ...}`` for
        something that is not an id
    """
    principal = caller.principal
    if id == PROTOCOL_ID:
        async with transaction_scope():
            # Built per person, never served from a constant: it carries the
            # persona, which is one person's engram and nobody else's.
            text = await build_instructions(principal)
        return {
            "id": PROTOCOL_ID,
            "title": PROTOCOL_TITLE,
            "text": text,
            "url": _docs_url(),
            "metadata": {"kind": "protocol"},
        }
    parts = _split_connector_id(id)
    if parts is None:
        return {"error": "bad_id", "detail": "not an id returned by search"}
    kind, space, locator = parts
    async with transaction_scope():
        try:
            if kind == "file":
                found = await _file(principal, id, space, locator)
            else:
                found = await _engram(principal, id, space, locator)
        # A space the caller has no name for: as good as nothing there.
        except AccessDenied:
            found = None
    return found if found is not None else {"error": "not_found"}


async def _file(principal: Principal, id: str, space: str, key: str) -> dict | None:
    """Fetch a file as text: ``fetch`` is a text contract.

    A connector asking for a PDF wants something it can read, not base64 it
    would spend its context on. That is the description and, with reading of
    files switched on, a blank line and the text Reef read from the file,
    every page marked, cut at a page so the whole stays within the engram
    size cap. A cut text says so in its last line: see :func:`_within`.

    :param principal: the caller
    :param id: the id asked for
    :param space: the caller's name for the space
    :param key: the file's key
    :returns: the connector payload, or None when there is no such file
    :raises AccessDenied: when the caller has no space of that name
    """
    attachment = await get_attachment(principal, space, key)
    if attachment is None:
        return None
    text = attachment.description or ""
    if get_settings().reading.enabled:
        pages = (await filetext.stored_pages([attachment.id]))[attachment.id]
        # Two characters for the blank line between description and text.
        room = max(0, get_settings().engrams.max_chars - len(text) - 2)
        read = _within(pages, room, f"{space}/file/{key}")
        if read:
            text = f"{text}\n\n{read}"
    return {
        "id": id,
        "title": attachment.filename or key,
        "text": text,
        "url": _app_url(space, ""),
        "metadata": {"cove": space, "kind": "file", "mime": attachment.mime},
    }


def _within(pages: list[filetext.StoredPage], room: int, ref: str) -> str:
    """Write a file's pages in at most ``room`` characters, saying where they stop.

    A text that does not fit is cut at a page, and its last line names the
    last page it holds and the ref ``open`` reads on from. Should the first
    page not fit whole, the line names that page instead. Room for the line
    is kept, so the whole stays within ``room``.

    :param pages: the file's pages, in page order
    :param room: the most characters
    :param ref: the file's ref, as ``open`` takes it
    :returns: the text; empty when there is none, or no room for any
    """
    text, after, inside = filetext.render_text(pages, cap=room)
    if after is None and inside is None:
        return text
    # No line names a page past the last, so these are the longest lines.
    last = pages[-1].number
    line = max(len(_stops_after(ref, last)), len(_stops_inside(ref, last)))
    text, after, inside = filetext.render_text(pages, cap=max(0, room - line - 2))
    if not text:
        return ""
    if after is not None:
        return f"{text}\n\n{_stops_after(ref, after)}"
    if inside is not None:
        return f"{text}\n\n{_stops_inside(ref, inside)}"
    return text


def _stops_after(ref: str, page: int) -> str:
    """Say that a text stops after a page, and how to read on.

    :param ref: the file's ref
    :param page: the last page the text holds
    :returns: the line
    """
    return (
        f"The text stops after page {page}. "
        f"To read on, use open with {ref}#page={page + 1}."
    )


def _stops_inside(ref: str, page: int) -> str:
    """Say that a text stops inside a page, and how to read it whole.

    :param ref: the file's ref
    :param page: the page the text stops in
    :returns: the line
    """
    return (
        f"The text stops partway through page {page}. "
        f"To read the whole page, use open with {ref}#page={page}."
    )


async def _engram(principal: Principal, id: str, space: str, path: str) -> dict | None:
    """Fetch an engram, and record that it was read.

    :param principal: the caller
    :param id: the id asked for
    :param space: the caller's name for the space
    :param path: the engram's path
    :returns: the connector payload, or None when there is no such engram
    :raises AccessDenied: when the caller has no space of that name
    """
    engram = await get_engram(principal, space, path)
    if engram is None:
        return None
    await usage.record(principal, engram.id, AccessKind.READ)
    replaced = await replacement_refs(principal, [engram.id])
    return {
        "id": id,
        "title": engram.title or path,
        "text": engram.body,
        "url": _app_url(space, path),
        "metadata": {
            "cove": space,
            "path": path,
            "kind": "page",
            **({"replaced_by": replaced[engram.id]} if engram.id in replaced else {}),
        },
    }
