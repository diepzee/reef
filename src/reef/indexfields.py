"""What the index shows for a page, derived from its body.

A module of its own, with no imports from the rest of reef, because both
ends of the page lifecycle need it: ``reef.pages`` stores these values when
a page is saved, and ``reef.context`` reads them back when it builds the
index. ``reef.context`` already depends on ``reef.pages`` by way of
``reef.coves``, so neither of those two modules can be its home.
"""

import re

_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
_INLINE_CODE_RE = re.compile(r"`+[^`]*`+")


def reference_targets(body: str) -> list[str]:
    """Extract distinct wiki-link targets from prose, exactly as written.

    References use the operating protocol's ``[[page.md]]`` and
    ``[[cove:page.md]]`` forms. Fenced and inline code are ignored: those
    commonly contain examples of the syntax and must not turn into graph
    edges.

    The targets are deliberately left unresolved. A cove's name is each
    reader's own alias for it, so what ``[[family:plan.md]]`` points at
    depends on who is reading. This half depends only on the body and can
    be stored with the page; :func:`resolve_references` is the half that
    depends on the reader.

    :param body: markdown page body
    :returns: ordered, de-duplicated targets
    """
    targets: list[str] = []
    seen: set[str] = set()
    fence: str | None = None

    for line in body.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is not None:
            continue

        prose = _INLINE_CODE_RE.sub("", line)
        for match in _WIKI_LINK_RE.finditer(prose):
            # A pipe is a conventional optional display label. It isn't part
            # of reef's documented form, but accepting it here is harmless and
            # avoids treating the label as part of a page path.
            target = match.group(1).split("|", 1)[0].strip()
            if not target or target in seen:
                continue
            seen.add(target)
            targets.append(target)
    return targets


def resolve_references(targets: list[str], source_alias: str) -> list[dict[str, str]]:
    """Resolve stored targets against one reader's name for the source cove.

    A target's existence and visibility are checked later, once the complete
    accessible index is known.

    :param targets: targets as returned by :func:`reference_targets`
    :param source_alias: alias used to resolve same-cove references
    :returns: ordered, de-duplicated ``cove`` / ``path`` target pairs
    """
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        if ":" in target:
            alias, path = (part.strip() for part in target.split(":", 1))
        else:
            alias, path = source_alias, target
        # Fragments identify a section of the same target page; the
        # page-level index and cove graph intentionally stop at the page.
        path = path.split("#", 1)[0]
        key = (alias, path)
        if not alias or not path or key in seen:
            continue
        seen.add(key)
        references.append({"cove": alias, "path": path})
    return references


def summary(body: str) -> str:
    """Return the page's one-line description: its first prose line.

    The page style mandates a short summary as the opening paragraph, so the
    first non-heading line is the curated description, not an arbitrary
    excerpt.

    :param body: the page's markdown body
    :returns: the first prose line, trimmed to 200 characters
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:200]
    return ""


def index_fields(body: str) -> dict:
    """Derive what the index shows for a page from its body.

    The one definition of those values. ``reef.pages.save_page`` stores the
    result with the page, and ``reef.context.build_index`` calls this for
    any page whose stored values are not current.

    :param body: the page's markdown body
    :returns: ``description``, ``size`` and ``reference_targets``
    """
    return {
        "description": summary(body),
        "size": len(body),
        "reference_targets": reference_targets(body),
    }
