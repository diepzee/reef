"""Links between engrams: stored when an engram is saved, read as backlinks.

Every ``[[wiki link]]`` in an engram body becomes a row in ``links``. That
turns "what points at this engram" from a scan of every body into a query,
and it is the structure later recall spreads along.

**Who can see a link.** Only a reader who can read both ends. Row security
enforces it, so nothing here filters by hand: a backlink from an engram the
reader cannot read is simply not returned, and is not counted.

**What a written target means.** ``[[plan.md]]`` names an engram in the same
space and means the same to everyone. ``[[family:plan.md]]`` names a space by
the *writer's* own name for it, and two people can use one name for
different spaces. So a cross-space target is resolved with the names of
whoever is saving, and where two writers mean different engrams by the same
words, both links exist. Each reader still sees only the links whose ends
they can read.

**When a link ends.** When its words leave the body. That is decided from
the text alone, by ``reef_retire_links``, so it does not depend on who is
saving or on what they can see. The row is kept, marked ``superseded``.

**What is never stored.** A link from a shared space into a personal one.
It would be hidden from the other members anyway, but it is refused
outright, here and again by the database.

Everything here assumes it runs inside :func:`reef.db.transaction_scope`.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from reef.access import Principal, accessible_spaces, alias_map
from reef.indexfields import index_fields
from reef.models import (
    Engram,
    Link,
    LinkKind,
    LinkStatus,
    MemberRole,
    Membership,
    Space,
    SpaceKind,
)
from reef.relations import MAX_RELATIONS, RELATION_KEYS, InvalidRelations, Relations


def parse_target(target: str) -> tuple[str | None, str]:
    """Split a written wiki-link target into a space name and an engram path.

    :param target: the target as written, for example ``family:plan.md#q3``
    :returns: the space name, or None for a same-space target, and the path
        without any ``#section`` part
    """
    alias: str | None = None
    path = target
    if ":" in target:
        alias, path = (part.strip() for part in target.split(":", 1))
    return alias, path.split("#", 1)[0].strip()


@dataclass
class Resolver:
    """One person's view of their spaces, for turning written targets into engrams."""

    principal: Principal
    spaces: dict[UUID, Space]
    space_by_alias: dict[str, UUID]
    writable: set[UUID]

    @classmethod
    async def for_person(cls, principal: Principal) -> "Resolver":
        """Load the principal's spaces, their names for them and their roles.

        :param principal: the person whose names resolve cross-space targets
        :returns: a resolver for that person
        """
        spaces = {space.id: space for space in await accessible_spaces(principal)}
        aliases = await alias_map(principal)
        member_of = await Membership.select(Membership.space_id).where(
            Membership.person_id == principal.person_id,
            Membership.role == MemberRole.MEMBER.value,
        )
        return cls(
            principal=principal,
            spaces=spaces,
            space_by_alias={alias: space_id for space_id, alias in aliases.items()},
            writable={row["space_id"] for row in member_of},
        )

    def wanted(self, source_space_id: UUID, targets: list[str]) -> dict:
        """Return where each written target points, as space and path.

        Targets naming a space this person has no name for are left out, and
        so is anything that would link a shared space into a personal one.

        :param source_space_id: the space of the engram the targets were written in
        :param targets: targets as written, in order
        :returns: ``(space id, path)`` to the first text that named it
        """
        source_is_personal = (
            self.spaces[source_space_id].kind == SpaceKind.PERSONAL.value
        )
        wanted: dict[tuple[UUID, str], str] = {}
        for text in targets:
            alias, path = parse_target(text)
            space_id = (
                source_space_id if alias is None else self.space_by_alias.get(alias)
            )
            if space_id is None or not path:
                continue
            target_is_personal = self.spaces[space_id].kind == SpaceKind.PERSONAL.value
            if target_is_personal and not source_is_personal:
                continue
            wanted.setdefault((space_id, path), text)
        return wanted


async def _insert(
    principal: Principal, from_engram_id: UUID, to_engram_id: UUID, text: str
) -> None:
    """Store one wiki link, unless the same link is already live.

    :param principal: the person recorded as the link's author
    :param from_engram_id: the engram the link was written in
    :param to_engram_id: the engram it points at
    :param text: the target as written
    """
    await Link.raw(
        "INSERT INTO links (id, from_engram_id, to_engram_id, target_text, author_id) "
        "VALUES ({}, {}, {}, {}, {}) ON CONFLICT DO NOTHING",
        uuid4(),
        from_engram_id,
        to_engram_id,
        text,
        principal.person_id,
    )


async def sync_wiki_links(
    resolver: Resolver, engram_id: UUID, space_id: UUID, targets: list[str]
) -> None:
    """Make the stored wiki links of one engram match its body.

    Call after the engram row carries its current ``reference_targets``: the
    retiring step reads them from the row.

    :param resolver: the saving person's view of their spaces
    :param engram_id: the engram that was saved
    :param space_id: its space
    :param targets: its wiki-link targets, as written
    """
    await Link.raw("SELECT reef_retire_links({})", engram_id)
    wanted = resolver.wanted(space_id, targets)
    if not wanted:
        return
    candidates = await Engram.select(Engram.id, Engram.space_id, Engram.path).where(
        Engram.space_id.is_in(list({space for space, _ in wanted})),
        Engram.path.is_in(list({path for _, path in wanted})),
    )
    live = {
        row["to_engram_id"]
        for row in await Link.select(Link.to_engram_id).where(
            Link.from_engram_id == engram_id,
            Link.status == LinkStatus.ACTIVE.value,
            Link.kind == LinkKind.REFERENCES.value,
        )
    }
    for row in candidates:
        text = wanted.get((row["space_id"], row["path"]))
        if text is None or row["id"] == engram_id or row["id"] in live:
            continue
        await _insert(resolver.principal, engram_id, row["id"], text)


async def connect_waiting_links(
    resolver: Resolver, engram_id: UUID, space_id: UUID, path: str
) -> None:
    """Link a newly created engram from the engrams that were already naming it.

    A wiki link can be written before its target exists. Nothing is stored
    for it then, so when the target appears the link has to be made here.

    Two kinds of waiting engram are connected, both exact:

    - Engrams in the **same space** that name the bare path. That means the
      same engram to everyone.
    - Engrams in the creator's **own personal space** that name it through a
      space name. Only the creator writes there, so the name is theirs.

    A cross-space target written in a *shared* space depends on its writer's
    names for their spaces, and is picked up when that engram is next saved.

    The links made here record the *creator* as their author, not whoever
    wrote the waiting words: the insert policy lets a person add links in
    their own name only. See ``Link.author_id``.

    :param resolver: the creating person's view of their spaces
    :param engram_id: the engram that was just created
    :param space_id: its space
    :param path: its path
    """
    own_personal = [
        space.id
        for space in resolver.spaces.values()
        if space.kind == SpaceKind.PERSONAL.value
        and space.owner_person_id == resolver.principal.person_id
    ]
    waiting = await Engram.raw(
        "SELECT p.id, p.space_id, t.text "
        "FROM engrams p, unnest(p.reference_targets) AS t(text) "
        "WHERE p.space_id = ANY({}::uuid[]) AND p.id <> {} "
        "AND p.indexed_version = p.version AND position({} in t.text) > 0",
        [str(candidate) for candidate in {space_id, *own_personal}],
        engram_id,
        path,
    )
    seen: set[UUID] = set()
    for row in waiting:
        if row["id"] in seen:
            continue
        alias, _ = parse_target(row["text"])
        if alias is not None and row["space_id"] not in own_personal:
            continue
        # ``wanted`` applies the same rules as a save would: whose name for
        # a space counts, and no link from a shared space into a personal one.
        if (space_id, path) not in resolver.wanted(row["space_id"], [row["text"]]):
            continue
        seen.add(row["id"])
        await _insert(resolver.principal, row["id"], engram_id, row["text"])


async def links_of(principal: Principal, engram_id: UUID) -> dict[str, list[dict]]:
    """Return what an engram links to and what links to it, for one reader.

    Spaces are named the way the reader names them.

    :param principal: the person reading
    :param engram_id: the engram
    :returns: ``links`` and ``linked_from``, each a list of ``space``,
        ``path``, ``title`` and ``kind``, ordered by space and path
    """
    aliases = await alias_map(principal)

    async def side(near: str, far: str) -> list[dict]:
        rows = await Link.raw(
            f"SELECT l.kind, p.space_id, p.path, p.title FROM links l "
            f"JOIN engrams p ON p.id = l.{far} "
            f"WHERE l.{near} = {{}} AND l.status = 'active'",
            engram_id,
        )
        entries = [
            {
                "space": aliases[row["space_id"]],
                "path": row["path"],
                "title": row["title"],
                "kind": row["kind"],
            }
            for row in rows
            if row["space_id"] in aliases
        ]
        return sorted(entries, key=lambda entry: (entry["space"], entry["path"]))

    return {
        "links": await side("from_engram_id", "to_engram_id"),
        "linked_from": await side("to_engram_id", "from_engram_id"),
    }


async def inbound_counts(engram_ids: list[UUID]) -> dict[UUID, int]:
    """Count the live links pointing at each engram, as the armed reader sees them.

    :param engram_ids: the engrams to count for
    :returns: engram id to count; engrams nothing points at are absent
    """
    if not engram_ids:
        return {}
    rows = await Link.raw(
        "SELECT to_engram_id, count(*) AS inbound FROM links "
        "WHERE status = 'active' AND to_engram_id = ANY({}::uuid[]) "
        "GROUP BY to_engram_id",
        [str(engram_id) for engram_id in engram_ids],
    )
    return {row["to_engram_id"]: row["inbound"] for row in rows}


async def backfill_links(principal: Principal) -> int:
    """Store the wiki links of every engram the principal may change.

    For engrams saved before links were stored. Engrams saved since already
    have theirs. Nothing a reader can observe about an engram changes: no
    version moves and no revision is written.

    The targets are read from the body rather than from the stored index
    fields, and the fields are stored along the way, so this does not
    depend on the index backfill having run first.

    :param principal: the person to act as
    :returns: how many engrams were brought up to date
    """
    resolver = await Resolver.for_person(principal)
    if not resolver.writable:
        return 0
    rows = await Engram.select(
        Engram.id, Engram.space_id, Engram.body, Engram.version
    ).where(
        Engram.space_id.is_in(list(resolver.writable)),
        Engram.linked_version.is_null() | (Engram.linked_version != Engram.version),
    )
    updated = 0
    for row in rows:
        fields = index_fields(row["body"])
        changed = (
            await Engram.update(
                {
                    Engram.description: fields["description"],
                    Engram.size: fields["size"],
                    Engram.reference_targets: fields["reference_targets"],
                    Engram.indexed_version: row["version"],
                    Engram.linked_version: row["version"],
                }
            )
            # As in the index backfill: if the engram moved on, that save
            # stored its own fields and links, and this row is left alone.
            .where(Engram.id == row["id"], Engram.version == row["version"])
            .returning(Engram.id)
        )
        if not changed:
            continue
        await sync_wiki_links(
            resolver, row["id"], row["space_id"], fields["reference_targets"]
        )
        updated += 1
    return updated


class RelationRefused(Exception):
    """A typed link cannot be saved with this writer's rights."""

    def __init__(self, code: str, target: str) -> None:
        """Keep the reason and written target, without any engram text.

        :param code: the reason the link was refused
        :param target: the link as written
        """
        self.code = code
        self.target = target
        super().__init__(f"{code}: {target}")


async def _relation_targets(
    resolver: Resolver, space_id: UUID, path: str, relations: Relations
) -> dict[LinkKind, dict[UUID, tuple[str, str, str]]]:
    """Resolve and check the targets of all given kinds.

    :param resolver: the writer's spaces and rights
    :param space_id: the source space
    :param path: the source path
    :param relations: the given kinds
    :returns: each kind's target ids, with written text, space name and path
    :raises RelationRefused: when a target or a replacement chain is invalid
    :raises InvalidRelations: for an unknown kind or too many targets
    """
    result: dict[LinkKind, dict[UUID, tuple[str, str, str]]] = {}
    aliases = {sid: alias for alias, sid in resolver.space_by_alias.items()}
    for kind, targets in relations.given.items():
        if kind not in RELATION_KEYS.values():
            raise InvalidRelations()
        if len(targets) > MAX_RELATIONS:
            raise InvalidRelations("relation_too_many")
        result[kind] = {}
        for text in targets:
            alias, target_path = parse_target(text)
            target_space = (
                space_id if alias is None else resolver.space_by_alias.get(alias)
            )
            if target_space is None or not target_path:
                raise RelationRefused("relation_target_missing", text)
            if target_space == space_id and target_path == path:
                raise RelationRefused("relation_to_itself", text)
            if (
                resolver.spaces[space_id].kind != SpaceKind.PERSONAL.value
                and resolver.spaces[target_space].kind == SpaceKind.PERSONAL.value
            ):
                raise RelationRefused("relation_into_personal", text)
            target = (
                await Engram.objects()
                .where(Engram.space_id == target_space, Engram.path == target_path)
                .first()
            )
            if target is None:
                raise RelationRefused("relation_target_missing", text)
            if kind == LinkKind.SUPERSEDES and target_space not in resolver.writable:
                raise RelationRefused("relation_needs_write", text)
            result[kind][target.id] = (text, aliases[target_space], target_path)
    supersedes = result.get(LinkKind.SUPERSEDES, {})
    if supersedes:
        circles = await Link.raw(
            "WITH RECURSIVE chain(root, id) AS ("
            "SELECT id, id FROM engrams WHERE id = ANY({}::uuid[]) "
            "UNION SELECT c.root, l.to_engram_id FROM chain c JOIN links l "
            "ON l.from_engram_id = c.id WHERE l.kind = 'supersedes' AND l.status = 'active') "
            "SELECT c.root FROM chain c JOIN engrams e ON e.id = c.id "
            "WHERE e.space_id = {} AND e.path = {}",
            [str(target) for target in supersedes],
            space_id,
            path,
        )
        if circles:
            raise RelationRefused("relation_circle", supersedes[circles[0]["root"]][0])
    return result


async def sync_typed_links(
    resolver: Resolver, engram_id: UUID, space_id: UUID, relations: Relations
) -> None:
    """Replace given kinds, retaining unseen links and unchanged rows.

    :param resolver: the writer's spaces and rights
    :param engram_id: the saved source engram
    :param space_id: its space
    :param relations: the kinds to replace
    :raises RelationRefused: when a link cannot be written
    """
    # Serialize graph changes before checking circles, including concurrent
    # saves of different sources. The lock lasts for the enclosing save.
    await Link.raw("SELECT pg_advisory_xact_lock(7360036)")
    source = await Engram.objects().get(Engram.id == engram_id)
    targets = await _relation_targets(resolver, space_id, source.path, relations)
    for kind, wanted in targets.items():
        await Link.raw(
            "SELECT reef_retire_typed_links({}, {}, {}::uuid[])",
            engram_id,
            kind.value,
            [str(target) for target in wanted],
        )
        for target_id, (text, _, _) in wanted.items():
            await Link.raw(
                "INSERT INTO links (id, from_engram_id, to_engram_id, target_text, "
                "author_id, kind, source) VALUES ({}, {}, {}, {}, {}, {}, 'author') "
                "ON CONFLICT DO NOTHING",
                uuid4(),
                engram_id,
                target_id,
                text,
                resolver.principal.person_id,
                kind.value,
            )


async def pending_replacements(
    resolver: Resolver, space_id: UUID, path: str, relations: Relations
) -> list[tuple[str, str]]:
    """Find newly added replacements, checking the whole relation block.

    :param resolver: the writer's spaces and rights
    :param space_id: the source space
    :param path: the source path, which may not exist yet
    :param relations: the given kinds
    :returns: new targets as sorted space-name and path pairs
    :raises RelationRefused: when a link cannot be written
    """
    wanted = (await _relation_targets(resolver, space_id, path, relations)).get(
        LinkKind.SUPERSEDES, {}
    )
    rows = await Link.raw(
        "SELECT l.to_engram_id FROM links l JOIN engrams e ON e.id = l.from_engram_id "
        "WHERE e.space_id = {} AND e.path = {} AND l.kind = 'supersedes' AND l.status = 'active'",
        space_id,
        path,
    )
    active = {row["to_engram_id"] for row in rows}
    return sorted(
        (alias, target_path)
        for target, (_, alias, target_path) in wanted.items()
        if target not in active
    )
