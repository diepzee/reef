"""Cut a stored file into the chunks that get embedded.

The same idea as :mod:`reef.sections`, for a file: one chunk for the file as
a whole, then one for each page.

**The whole-file chunk** carries the filename, the description and the start
of the text, up to ``REEF_EMBEDDING_ENGRAM_CHARS``. Every file has it, even
one without text, so a photo or a plan is still found by meaning.

**A page** is a chunk of its own, anchored ``p<N>``. A page longer than
``REEF_EMBEDDING_PIECE_MAX_CHARS`` is cut between paragraphs into ``p<N>``,
``p<N>~2`` and so on. A page shorter than ``REEF_EMBEDDING_PIECE_MIN_CHARS``
says too little alone and joins the chunk before it. A page with no words is
no chunk. A file of more than one page names the page in each chunk's
context, as ``page 2``.

This module is pure. It reads no table and logs nothing.
"""

import re

from reef import sections
from reef.config import get_settings
from reef.sections import Section

_ANCHOR = re.compile(r"p(\d+)(?:~\d+)?")


def page_of(anchor: str | None) -> int | None:
    """Read the page number out of a chunk's anchor.

    :param anchor: a chunk anchor, or None for the whole-file chunk
    :returns: the page number, or None for the whole-file chunk and for an
        anchor that is not a page's
    """
    match = _ANCHOR.fullmatch(anchor) if anchor else None
    return int(match.group(1)) if match else None


def whole(filename: str, description: str, start: str) -> Section:
    """Build the whole-file chunk.

    :param filename: the file's name
    :param description: the file's description
    :param start: the start of the file's text: the text of its pages with
        words, stripped and joined by a blank line. Only the first
        ``REEF_EMBEDDING_ENGRAM_CHARS`` characters are used.
    :returns: the chunk, anchored None
    """
    limit = get_settings().embedding.engram_chars
    return sections.piece(None, (), start[:limit], filename, description)


def page_chunks(
    filename: str,
    description: str,
    pages: list[tuple[int, str]],
    page_count: int | None = None,
) -> list[Section]:
    """Cut pages into their chunks, without the whole-file chunk.

    ``pages`` may be only some of a file's pages: a page that starts a chunk
    and the pages after it, up to the next page that starts one. Its chunks
    are then exactly the ones :func:`split` gives for the whole file, as
    long as ``page_count`` says how many pages the file has.

    :param filename: the file's name
    :param description: the file's description
    :param pages: the page number and text of each page, in order
    :param page_count: how many pages the file has; ``len(pages)`` when None.
        It decides whether a chunk names its page.
    :returns: a chunk for each page that has words, in order
    """
    sizes = get_settings().embedding
    texts = [(number, text.strip()) for number, text in pages]
    texts = [(number, text) for number, text in texts if text]
    many = (len(pages) if page_count is None else page_count) > 1
    chunks: list[tuple[str, tuple[str, ...], str]] = []
    for number, text in texts:
        where = (f"page {number}",) if many else ()
        if len(text) < sizes.piece_min_chars and chunks:
            anchor, head, before = chunks[-1]
            label = f"{where[0]}\n\n" if where else ""
            chunks[-1] = (anchor, head, f"{before}\n\n{label}{text}")
            continue
        if len(text) < sizes.piece_min_chars:
            # Nothing before it but the whole-file chunk, which holds it.
            continue
        for part, body in enumerate(sections.cut(text, sizes.piece_max_chars), 1):
            anchor = f"p{number}" if part == 1 else f"p{number}~{part}"
            chunks.append((anchor, where, body))
    return [
        sections.piece(anchor, where, body, filename, description)
        for anchor, where, body in chunks
    ]


def split(
    filename: str, description: str, pages: list[tuple[int, str]]
) -> list[Section]:
    """Cut a file into the chunks that get embedded.

    :param filename: the file's name
    :param description: the file's description
    :param pages: the page number and text of each page, in order
    :returns: the whole-file chunk first, then a chunk for each page that has
        words. A file without text gives the whole-file chunk alone.
    """
    texts = [text.strip() for _, text in pages]
    start = "\n\n".join(text for text in texts if text)
    return [whole(filename, description, start)] + page_chunks(
        filename, description, pages
    )
