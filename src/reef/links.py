"""Links between pages: stored when a page is saved, read as backlinks.

Every ``[[wiki link]]`` in a page body becomes a row in ``links``. That
turns "what points at this page" from a scan of every body into a query,
and it is the structure later recall spreads along.

**Who can see a link.** Only a reader who can read both ends. Row security
enforces it, so nothing here filters by hand: a backlink from a page the
reader cannot read is simply not returned, and is not counted.

**What a written target means.** ``[[plan.md]]`` names a page in the same
cove and means the same to everyone. ``[[family:plan.md]]`` names a cove by
the *writer's* own name for it, and two people can use one name for
different coves. So a cross-cove target is resolved with the names of
whoever is saving, and where two writers mean different pages by the same
words, both links exist. Each reader still sees only the links whose ends
they can read.

**When a link ends.** When its words leave the body. That is decided from
the text alone, by ``reef_retire_links``, so it does not depend on who is
saving or on what they can see. The row is kept, marked ``superseded``.

**What is never stored.** A link from a shared cove into a personal one.
It would be hidden from the other members anyway, but it is refused
outright, here and again by the database.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from reef.access import Principal, accessible_coves, alias_map
from reef.indexfields import index_fields
from reef.models import (
    Cove,
    CoveKind,
    Link,
    LinkStatus,
    MemberRole,
    Membership,
    Page,
)


def parse_target(target: str) -> tuple[str | None, str]:
    """Split a written wiki-link target into a cove name and a page path.

    :param target: the target as written, for example ``family:plan.md#q3``
    :returns: the cove name, or None for a same-cove target, and the path
        without any ``#section`` part
    """
    alias: str | None = None
    path = target
    if ":" in target:
        alias, path = (part.strip() for part in target.split(":", 1))
    return alias, path.split("#", 1)[0].strip()


@dataclass
class Resolver:
    """One person's view of their coves, for turning written targets into pages."""

    principal: Principal
    coves: dict[UUID, Cove]
    cove_by_alias: dict[str, UUID]
    writable: set[UUID]

    @classmethod
    async def for_person(cls, principal: Principal) -> "Resolver":
        """Load the principal's coves, their names for them and their roles.

        :param principal: the person whose names resolve cross-cove targets
        :returns: a resolver for that person
        """
        coves = {cove.id: cove for cove in await accessible_coves(principal)}
        aliases = await alias_map(principal)
        member_of = await Membership.select(Membership.cove_id).where(
            Membership.person_id == principal.person_id,
            Membership.role == MemberRole.MEMBER.value,
        )
        return cls(
            principal=principal,
            coves=coves,
            cove_by_alias={alias: cove_id for cove_id, alias in aliases.items()},
            writable={row["cove_id"] for row in member_of},
        )

    def wanted(self, source_cove_id: UUID, targets: list[str]) -> dict:
        """Return where each written target points, as cove and path.

        Targets naming a cove this person has no name for are left out, and
        so is anything that would link a shared cove into a personal one.

        :param source_cove_id: the cove of the page the targets were written in
        :param targets: targets as written, in order
        :returns: ``(cove id, path)`` to the first text that named it
        """
        source_is_personal = self.coves[source_cove_id].kind == CoveKind.PERSONAL.value
        wanted: dict[tuple[UUID, str], str] = {}
        for text in targets:
            alias, path = parse_target(text)
            cove_id = source_cove_id if alias is None else self.cove_by_alias.get(alias)
            if cove_id is None or not path:
                continue
            target_is_personal = self.coves[cove_id].kind == CoveKind.PERSONAL.value
            if target_is_personal and not source_is_personal:
                continue
            wanted.setdefault((cove_id, path), text)
        return wanted


async def _insert(
    principal: Principal, from_page_id: UUID, to_page_id: UUID, text: str
) -> None:
    """Store one wiki link, unless the same link is already live.

    :param principal: the person recorded as the link's author
    :param from_page_id: the page the link was written in
    :param to_page_id: the page it points at
    :param text: the target as written
    """
    await Link.raw(
        "INSERT INTO links (id, from_page_id, to_page_id, target_text, author_id) "
        "VALUES ({}, {}, {}, {}, {}) ON CONFLICT DO NOTHING",
        uuid4(),
        from_page_id,
        to_page_id,
        text,
        principal.person_id,
    )


async def sync_wiki_links(
    resolver: Resolver, page_id: UUID, cove_id: UUID, targets: list[str]
) -> None:
    """Make the stored wiki links of one page match its body.

    Call after the page row carries its current ``reference_targets``: the
    retiring step reads them from the row.

    :param resolver: the saving person's view of their coves
    :param page_id: the page that was saved
    :param cove_id: its cove
    :param targets: its wiki-link targets, as written
    """
    await Link.raw("SELECT reef_retire_links({})", page_id)
    wanted = resolver.wanted(cove_id, targets)
    if not wanted:
        return
    candidates = await Page.select(Page.id, Page.cove_id, Page.path).where(
        Page.cove_id.is_in(list({cove for cove, _ in wanted})),
        Page.path.is_in(list({path for _, path in wanted})),
    )
    live = {
        row["to_page_id"]
        for row in await Link.select(Link.to_page_id).where(
            Link.from_page_id == page_id,
            Link.status == LinkStatus.ACTIVE.value,
        )
    }
    for row in candidates:
        text = wanted.get((row["cove_id"], row["path"]))
        if text is None or row["id"] == page_id or row["id"] in live:
            continue
        await _insert(resolver.principal, page_id, row["id"], text)


async def connect_waiting_links(
    resolver: Resolver, page_id: UUID, cove_id: UUID, path: str
) -> None:
    """Link a newly created page from the pages that were already naming it.

    A wiki link can be written before its target exists. Nothing is stored
    for it then, so when the target appears the link has to be made here.

    Two kinds of waiting page are connected, both exact:

    - Pages in the **same cove** that name the bare path. That means the
      same page to everyone.
    - Pages in the creator's **own personal cove** that name it through a
      cove name. Only the creator writes there, so the name is theirs.

    A cross-cove target written in a *shared* cove depends on its writer's
    names for their coves, and is picked up when that page is next saved.

    The links made here record the *creator* as their author, not whoever
    wrote the waiting words: the insert policy lets a person add links in
    their own name only. See ``Link.author_id``.

    :param resolver: the creating person's view of their coves
    :param page_id: the page that was just created
    :param cove_id: its cove
    :param path: its path
    """
    own_personal = [
        cove.id
        for cove in resolver.coves.values()
        if cove.kind == CoveKind.PERSONAL.value
        and cove.owner_person_id == resolver.principal.person_id
    ]
    waiting = await Page.raw(
        "SELECT p.id, p.cove_id, t.text "
        "FROM pages p, unnest(p.reference_targets) AS t(text) "
        "WHERE p.cove_id = ANY({}::uuid[]) AND p.id <> {} "
        "AND p.indexed_version = p.version AND position({} in t.text) > 0",
        [str(candidate) for candidate in {cove_id, *own_personal}],
        page_id,
        path,
    )
    seen: set[UUID] = set()
    for row in waiting:
        if row["id"] in seen:
            continue
        alias, _ = parse_target(row["text"])
        if alias is not None and row["cove_id"] not in own_personal:
            continue
        # ``wanted`` applies the same rules as a save would: whose name for
        # a cove counts, and no link from a shared cove into a personal one.
        if (cove_id, path) not in resolver.wanted(row["cove_id"], [row["text"]]):
            continue
        seen.add(row["id"])
        await _insert(resolver.principal, row["id"], page_id, row["text"])


async def links_of(principal: Principal, page_id: UUID) -> dict[str, list[dict]]:
    """Return what a page links to and what links to it, for one reader.

    Coves are named the way the reader names them.

    :param principal: the person reading
    :param page_id: the page
    :returns: ``links`` and ``linked_from``, each a list of ``cove``,
        ``path``, ``title`` and ``kind``, ordered by cove and path
    """
    aliases = await alias_map(principal)

    async def side(near: str, far: str) -> list[dict]:
        rows = await Link.raw(
            f"SELECT l.kind, p.cove_id, p.path, p.title FROM links l "
            f"JOIN pages p ON p.id = l.{far} "
            f"WHERE l.{near} = {{}} AND l.status = 'active'",
            page_id,
        )
        entries = [
            {
                "cove": aliases[row["cove_id"]],
                "path": row["path"],
                "title": row["title"],
                "kind": row["kind"],
            }
            for row in rows
            if row["cove_id"] in aliases
        ]
        return sorted(entries, key=lambda entry: (entry["cove"], entry["path"]))

    return {
        "links": await side("from_page_id", "to_page_id"),
        "linked_from": await side("to_page_id", "from_page_id"),
    }


async def inbound_counts(page_ids: list[UUID]) -> dict[UUID, int]:
    """Count the live links pointing at each page, as the armed reader sees them.

    :param page_ids: the pages to count for
    :returns: page id to count; pages nothing points at are absent
    """
    if not page_ids:
        return {}
    rows = await Link.raw(
        "SELECT to_page_id, count(*) AS inbound FROM links "
        "WHERE status = 'active' AND to_page_id = ANY({}::uuid[]) "
        "GROUP BY to_page_id",
        [str(page_id) for page_id in page_ids],
    )
    return {row["to_page_id"]: row["inbound"] for row in rows}


async def backfill_links(principal: Principal) -> int:
    """Store the wiki links of every page the principal may change.

    For pages saved before links were stored. Pages saved since already
    have theirs. Nothing a reader can observe about a page changes: no
    version moves and no revision is written.

    The targets are read from the body rather than from the stored index
    fields, and the fields are stored along the way, so this does not
    depend on the index backfill having run first.

    :param principal: the person to act as
    :returns: how many pages were brought up to date
    """
    resolver = await Resolver.for_person(principal)
    if not resolver.writable:
        return 0
    rows = await Page.select(Page.id, Page.cove_id, Page.body, Page.version).where(
        Page.cove_id.is_in(list(resolver.writable)),
        Page.linked_version.is_null() | (Page.linked_version != Page.version),
    )
    updated = 0
    for row in rows:
        fields = index_fields(row["body"])
        changed = (
            await Page.update(
                {
                    Page.description: fields["description"],
                    Page.size: fields["size"],
                    Page.reference_targets: fields["reference_targets"],
                    Page.indexed_version: row["version"],
                    Page.linked_version: row["version"],
                }
            )
            # As in the index backfill: if the page moved on, that save
            # stored its own fields and links, and this row is left alone.
            .where(Page.id == row["id"], Page.version == row["version"])
            .returning(Page.id)
        )
        if not changed:
            continue
        await sync_wiki_links(
            resolver, row["id"], row["cove_id"], fields["reference_targets"]
        )
        updated += 1
    return updated
