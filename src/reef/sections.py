"""Cut a page into the pieces that get embedded.

A page is searched by meaning through its sections, not as one block: a
question is usually answered by one passage, and one vector for a long page
blurs every passage in it together.

**What the pieces are.** A page that fits in one piece is one piece. A
longer page gets one piece for the page as a whole, cut at
:data:`PAGE_CHARS`, and one for each section under a level 2 or level 3
heading. Deeper headings stay inside their parent. Text before the first
such heading is a section of its own.

**Nothing is dropped.** A section shorter than :data:`MIN_CHARS` says too
little to embed on its own, so it joins the section before it. A section
longer than :data:`MAX_CHARS` is cut between paragraphs into parts.

**What is embedded.** Not the bare text. Each piece starts with the page's
title, its one-line description and the headings above it. A passage like
"Renewed in March, costs 45 a month" means little alone and a lot under
"Insurance > Car". This is contextual retrieval without a model call: the
context is already on the page.

**When to embed again.** Each piece carries a digest of exactly the text
that is embedded. Unchanged text has an unchanged digest, so an edit to one
section costs two embeddings, that section and the whole-page piece, not
one per section. An edit beyond :data:`PAGE_CHARS` costs one.

Sections are not stored as rows of their own. They exist only as the
embedding rows made from them: see the memory engine design, "The unit is
the engram".

This module is pure. It reads no table and knows no model.
"""

import hashlib
import re
from dataclasses import dataclass

#: A section shorter than this joins the one before it.
MIN_CHARS = 200

#: A section longer than this is cut into parts. The size the retrieval
#: experiment of 18 November 2026 was measured with.
MAX_CHARS = 2000

#: How much of a long page the whole-page piece holds.
PAGE_CHARS = 8000

#: The anchor of the text before a page's first section heading. A heading
#: cannot produce it: a slug holds no tilde.
INTRO = "~intro"

#: Deliberately simple. An earlier pattern also matched the optional closing
#: ``#`` marks, and took cubic time on a line of alternating marks and
#: spaces: one crafted heading could occupy the worker for hours. The
#: closing marks are stripped in code instead. See :func:`_heading`.
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$")

#: A line longer than this is not a heading, whatever it starts with.
_HEADING_CHARS = 300
_FENCE = re.compile(r"^\s*(```|~~~)")
_NOT_SLUG = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True)
class Section:
    """One piece of a page, as it is embedded.

    ``anchor`` names the piece within its page and is stable while its
    headings are: None for the page as a whole, :data:`INTRO` for the text
    before the first heading, otherwise the heading path as a slug. A later
    part of a long section ends in ``~2``, ``~3`` and so on.
    """

    anchor: str | None
    heading_path: tuple[str, ...]
    body: str
    text: str
    content_hash: str


def _slug(heading: str) -> str:
    """Turn a heading into the form used in an anchor.

    :param heading: the heading text
    :returns: lower case, words joined by hyphens
    """
    return _NOT_SLUG.sub("-", heading.lower()).strip("-") or "section"


def _heading(line: str) -> tuple[int, str] | None:
    """Read a line as a heading, if it is one.

    :param line: one line of the page
    :returns: the level and the heading text, or None
    """
    if len(line) > _HEADING_CHARS:
        return None
    match = _HEADING.match(line)
    if match is None:
        return None
    text = match.group(2).rstrip().rstrip("#").rstrip()
    return (len(match.group(1)), text) if text else None


def _blocks(body: str) -> list[tuple[tuple[str, ...], str]]:
    """Split a body at its level 1 to 3 headings.

    A ``#`` inside a fenced code block is code, not a heading.

    :param body: the page's markdown
    :returns: the heading path and the text of each block, in page order.
        The text does not repeat the heading. A level 1 heading starts a
        new path but is not a section of its own.
    """
    blocks: list[tuple[tuple[str, ...], list[str]]] = [((), [])]
    path: dict[int, str] = {}
    fence: str | None = None
    for line in body.splitlines():
        mark = _FENCE.match(line)
        if mark:
            if fence is None:
                fence = mark.group(1)
            elif mark.group(1) == fence:
                fence = None
        heading = _heading(line) if fence is None else None
        if heading is None or heading[0] > 3:
            blocks[-1][1].append(line)
            continue
        level, text = heading
        path = {k: v for k, v in path.items() if k < level}
        path[level] = text
        # Level 1 is the page's own title: it scopes nothing below it.
        blocks.append((tuple(path[k] for k in sorted(path) if k > 1), []))
    return [(where, "\n".join(lines).strip()) for where, lines in blocks]


def _parts(text: str) -> list[str]:
    """Cut a long text between paragraphs.

    :param text: a section's text
    :returns: parts of at most :data:`MAX_CHARS` characters. A single
        paragraph longer than that is cut where it has to be.
    """
    parts: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        while len(paragraph) > MAX_CHARS:
            if current:
                parts.append(current)
                current = ""
            parts.append(paragraph[:MAX_CHARS])
            paragraph = paragraph[MAX_CHARS:]
        if current and len(current) + len(paragraph) + 2 > MAX_CHARS:
            parts.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    if current.strip():
        parts.append(current)
    return parts


def _piece(
    anchor: str | None,
    heading_path: tuple[str, ...],
    body: str,
    title: str,
    description: str,
) -> Section:
    """Build one piece, with the context in front of its text.

    :param anchor: the piece's anchor
    :param heading_path: the headings above it
    :param body: its own text
    :param title: the page's title
    :param description: the page's one-line description
    :returns: the piece
    """
    context = [title]
    # A piece that opens with the description already says it.
    if description and not body.lstrip().startswith(description):
        context.append(description)
    if heading_path:
        context.append(" > ".join(heading_path))
    text = "\n".join(line for line in context if line) + "\n\n" + body
    return Section(
        anchor=anchor,
        heading_path=heading_path,
        body=body,
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def split(title: str, description: str, body: str) -> list[Section]:
    """Cut a page into the pieces that get embedded.

    :param title: the page's title
    :param description: the page's one-line description, as the index shows it
    :param body: the page's markdown
    :returns: the whole-page piece first, then the sections in page order.
        A page that fits in one piece gives one piece. An empty page gives
        none.
    """
    body = body.strip()
    if not body:
        return []
    whole = _piece(None, (), body[:PAGE_CHARS], title, description)
    if len(body) <= MAX_CHARS:
        return [whole]

    # Join each short block to the one before it. A short opening block
    # has nothing before it and joins the one after.
    merged: list[tuple[tuple[str, ...], str]] = []
    carry = ""
    for where, text in _blocks(body):
        label = f"{where[-1]}\n\n" if where else ""
        if not text and not label:
            continue
        if len(text) < MIN_CHARS:
            if merged:
                head, before = merged[-1]
                merged[-1] = (head, f"{before}\n\n{label}{text}".strip())
            else:
                carry = f"{carry}\n\n{label}{text}".strip()
            continue
        merged.append((where, f"{carry}\n\n{text}".strip() if carry else text))
        carry = ""
    if carry:
        merged.append(((), carry))

    pieces = [whole]
    seen: dict[str, int] = {}
    for where, text in merged:
        base = "/".join(_slug(heading) for heading in where) or INTRO
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            base = f"{base}-{seen[base]}"
        for number, part in enumerate(_parts(text), 1):
            anchor = base if number == 1 else f"{base}~{number}"
            pieces.append(_piece(anchor, where, part, title, description))
    return pieces
