# Multi-User Spaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spaces become abstract named groups of people — any person in any number of spaces, joined by email-bound invitation — per `docs/superpowers/specs/2026-08-07-multi-user-spaces-design.md`.

**Architecture:** The membership table and RLS already enforce N-member spaces; this plan replaces the two-alias addressing layer with slug addressing, adds runtime space/member administration (`create_space`, `invite`, `remove_member`), auto-onboards new people at first sign-in, generalizes sharing to any destination space, and prepares (but does not enable) read-only roles in the RLS write predicate.

**Tech Stack:** Python 3.12+, SQLAlchemy 2 async, Postgres 17 (RLS), Alembic, FastMCP, pytest-asyncio, uv.

> **Port note — read this before following any code block.** This plan was
> written and executed against the SQLAlchemy build of `rif`, preserved at tag
> `multi-user-spaces-sqlalchemy`. The repository has since moved to Piccolo, so
> the *behavior* every task specifies is what landed, but the code shape did
> not: there is no `session` to thread (Piccolo binds queries to the ambient
> transaction opened by `rif.db.transaction_scope`), models are Piccolo
> `Table`s rather than a declarative `Base`, and Task 8's Alembic revision is
> instead `src/rif/piccolo_migrations/rif_2026_08_08t10_00_00_000000.py`
> (migration id `2026-08-08T10:00:00:000000`). The work reached this branch as
> three ports — chunk A (models, RLS, conftest), chunk B (slug addressing,
> space administration, onboarding, protocol), chunk C (sharing to any space,
> the tool surface, the migration, these docs) — so the task boundaries below
> are the specification, not the commit history. Read the code blocks as
> intent; read `src/rif/` for the shape.

## Global Constraints

- Modern Python, modern types (`X | None`, no `typing.Optional`). Docstrings are **mandatory** on every function, ReST-style (`:param:`/`:returns:`/`:raises:`), no types in docstrings.
- Match the existing code style: ~88-col, terse, comments only for non-obvious constraints.
- Tests need Postgres: `docker compose up -d` once, then `uv run pytest -q`. The test schema is rebuilt from `rif.models.Base.metadata` + `rif.rls` per session — model changes reach tests without migrations.
- TDD every task: failing test → minimal code → pass → commit.
- Every task ends with the **full** suite green (`uv run pytest -q`), not just the new tests.
- Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Docs (Task 9) follow ISO 24495-1 plain language: reader-first, one idea per sentence, active voice.

---

### Task 1: Models — SHARED kind, member roles, owned spaces, person audit columns

**Files:**
- Modify: `src/rif/models.py`
- Modify: `src/rif/access.py:9` (compat: alias dict only)
- Modify: `src/rif/context.py:9,96,111` (compat: alias dict only)
- Modify: `src/rif/server.py:132` (compat: alias lookup only)
- Modify: `tests/conftest.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `SpaceKind.SHARED` (replaces `HOUSEHOLD`), `MemberRole` StrEnum (`MEMBER`, `VIEWER`), `Membership.role: Mapped[MemberRole]`, `Space.owner_person_id: Mapped[UUID]` (NOT NULL, partial-unique for PERSONAL), `Person.invited_by_person_id: Mapped[UUID | None]`, `Person.created_at: Mapped[datetime]`, conftest `graph` fixture with `person(email, display_name)`, `personal_space(owner, slug=None)`, `shared_space(slug, owner, *members)` builders.
- Consumes: nothing new.

**Note:** `Promotion.dest_space_id` is deliberately NOT added here — it lands in Task 5 together with the promotion-logic rework, so the suite stays green between tasks. The alias-dict edits here are temporary compatibility shims that Task 3 deletes.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_schema.py`:

```python
async def test_membership_role_defaults_to_member(session, household):
    from rif.models import MemberRole, Membership

    row = await session.get(
        Membership, (household["wouter"].id, household["shared"].id)
    )
    assert row.role is MemberRole.MEMBER


async def test_one_personal_space_per_person_is_a_db_invariant(session, household):
    import pytest
    from sqlalchemy.exc import IntegrityError

    from rif.models import Space, SpaceKind

    session.add(
        Space(
            slug="second-personal",
            kind=SpaceKind.PERSONAL,
            owner_person_id=household["wouter"].id,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_person_may_own_many_shared_spaces(session, household):
    from rif.models import Space, SpaceKind

    session.add_all(
        [
            Space(
                slug="trip",
                kind=SpaceKind.SHARED,
                owner_person_id=household["wouter"].id,
            ),
            Space(
                slug="admin",
                kind=SpaceKind.SHARED,
                owner_person_id=household["wouter"].id,
            ),
        ]
    )
    await session.flush()  # must not raise: the old global UNIQUE is gone
```

- [ ] **Step 2: Run to verify failure**

Run: `docker compose up -d && uv run pytest tests/test_schema.py -q`
Expected: FAIL (`MemberRole` import error).

- [ ] **Step 3: Update `src/rif/models.py`**

Replace the `SpaceKind` enum and add `MemberRole` (keep `AttachmentStatus` as is):

```python
class SpaceKind(StrEnum):
    """The two kinds of space: one private per person, any number shared."""

    PERSONAL = "personal"
    SHARED = "shared"


class MemberRole(StrEnum):
    """What a membership grants. VIEWER is dormant until invites can grant it."""

    MEMBER = "member"
    VIEWER = "viewer"
```

Update `Person` (docstring stays, add columns; `func` is already imported):

```python
class Person(Base):
    """A human principal. Provider subject is durable identity; email binds it."""

    __tablename__ = "persons"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(unique=True)
    subject: Mapped[str | None] = mapped_column(unique=True)
    display_name: Mapped[str]
    invited_by_person_id: Mapped[UUID | None] = mapped_column(ForeignKey("persons.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

Update `Space` — owner required, uniqueness only for personal spaces (add `Index` and `text` to the sqlalchemy import):

```python
class Space(Base):
    """A named group of people. Every space has one accountable owner."""

    __tablename__ = "spaces"
    __table_args__ = (
        Index(
            "uq_personal_owner_person",
            "owner_person_id",
            unique=True,
            postgresql_where=text("kind = 'PERSONAL'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[SpaceKind]
    owner_person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"))
    version: Mapped[int] = mapped_column(default=0)
```

Update `Membership`:

```python
class Membership(Base):
    """Who may see which space, and what the membership grants."""

    __tablename__ = "memberships"

    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), primary_key=True)
    space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.id"), primary_key=True)
    role: Mapped[MemberRole] = mapped_column(
        default=MemberRole.MEMBER, server_default="MEMBER"
    )
```

- [ ] **Step 4: Compatibility shims (deleted in Task 3)**

`src/rif/access.py:9`:

```python
_ALIASES = {"personal": SpaceKind.PERSONAL, "household": SpaceKind.SHARED}
```

`src/rif/context.py:9`:

```python
_ALIAS_BY_KIND = {SpaceKind.PERSONAL: "personal", SpaceKind.SHARED: "household"}
```

`src/rif/context.py:111-112` (index version string — alias, not kind value, so the payload keeps saying `household`):

```python
version = (
    ";".join(f"{_ALIAS_BY_KIND[space.kind]}={space.version}" for space in spaces)
    or "empty"
)
```

`src/rif/server.py:132` (`tool_list_spaces`) — import `_ALIAS_BY_KIND` from `rif.context` and use it:

```python
return [
    {"alias": _ALIAS_BY_KIND[s.kind], "version": s.version}
    for s in await accessible_spaces(session, principal)
]
```

- [ ] **Step 5: Rebuild `tests/conftest.py` fixtures** — replace the `household` fixture with a builder plus a two-person default (imports gain `MemberRole` only if used; builders rely on the model default):

```python
@pytest_asyncio.fixture
async def graph(session):
    """Factory for arbitrary person/space/membership topologies.

    :returns: an object with ``person``, ``personal_space`` and
        ``shared_space`` coroutine builders
    """

    class _Graph:
        async def person(self, email: str, display_name: str) -> Person:
            """Create one person row."""
            row = Person(email=email, display_name=display_name)
            session.add(row)
            await session.flush()
            return row

        async def personal_space(self, owner: Person, slug: str | None = None) -> Space:
            """Create a personal space plus its single membership."""
            space = Space(
                slug=slug or f"personal-{owner.id.hex}",
                kind=SpaceKind.PERSONAL,
                owner_person_id=owner.id,
            )
            session.add(space)
            await session.flush()
            session.add(Membership(person_id=owner.id, space_id=space.id))
            await session.flush()
            return space

        async def shared_space(
            self, slug: str, owner: Person, *members: Person
        ) -> Space:
            """Create a shared space owned by ``owner``, with memberships."""
            space = Space(slug=slug, kind=SpaceKind.SHARED, owner_person_id=owner.id)
            session.add(space)
            await session.flush()
            session.add_all(
                [
                    Membership(person_id=p.id, space_id=space.id)
                    for p in (owner, *members)
                ]
            )
            await session.flush()
            return space

    return _Graph()


@pytest_asyncio.fixture
async def household(graph) -> dict:
    """Two people, two personal spaces, one shared space they both belong to.

    :returns: mapping with keys ``wouter``, ``partner``, ``w_personal``,
        ``p_personal``, ``shared``
    """
    wouter = await graph.person("wouter@example.test", "Wouter")
    partner = await graph.person("partner@example.test", "Partner")
    w_personal = await graph.personal_space(wouter, slug="wouter")
    p_personal = await graph.personal_space(partner, slug="partner")
    shared = await graph.shared_space("school", wouter, partner)
    return {
        "wouter": wouter,
        "partner": partner,
        "w_personal": w_personal,
        "p_personal": p_personal,
        "shared": shared,
    }
```

- [ ] **Step 6: Full suite green**

Run: `uv run pytest -q`
Expected: PASS (aliases still resolve `household` → the SHARED space; `resolve_space`'s PERSONAL branch is untouched).

- [ ] **Step 7: Commit**

```bash
git add src/rif/models.py src/rif/access.py src/rif/context.py src/rif/server.py tests/conftest.py tests/test_schema.py
git commit -m "feat: model groundwork for multi-user spaces — SHARED kind, member roles, owned spaces"
```

---

### Task 2: Role-aware RLS write predicate (viewers dormant but enforced)

**Files:**
- Modify: `src/rif/rls.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Produces: RLS policies where `USING` = any membership, `WITH CHECK` = membership with `role = 'MEMBER'`. Consumed by conftest schema build and the Task 8 migration.

- [ ] **Step 1: Write the failing test** — append to `tests/test_security.py`:

```python
async def test_viewer_row_can_read_but_never_write(session, household, graph):
    """A hand-inserted VIEWER membership reads the space but cannot write it.

    Nothing creates viewers yet; this pins the RLS split so enabling
    read-only roles later is app-level work, not a policy migration.
    """
    import pytest
    from sqlalchemy.exc import ProgrammingError

    from rif.models import MemberRole, Membership

    page = await _seed_shared_page(session, household)
    anna = await graph.person("anna@example.test", "Anna")
    await graph.personal_space(anna)
    session.add(
        Membership(
            person_id=anna.id, space_id=household["shared"].id, role=MemberRole.VIEWER
        )
    )
    await session.flush()

    await resolve_space(session, principal_for(anna), "personal")  # arms RLS
    visible = (await session.scalars(select(Page).where(Page.id == page.id))).all()
    assert [p.id for p in visible] == [page.id]

    session.add(
        Page(
            space_id=household["shared"].id,
            path="planted.md",
            title="x",
            body="viewer write",
        )
    )
    with pytest.raises(ProgrammingError):
        await session.flush()
```

Add the shared-page seeder next to `_seed_private_page`:

```python
async def _seed_shared_page(session, household) -> Page:
    await resolve_space(session, principal_for(household["wouter"]), "personal")
    page = Page(
        space_id=household["shared"].id,
        path="joint.md",
        title="Joint",
        body="shared detail",
    )
    session.add(page)
    await session.flush()
    return page
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_security.py -q`
Expected: FAIL — the viewer's forged insert succeeds (no `ProgrammingError`), because `WITH CHECK` doesn't look at role yet.

- [ ] **Step 3: Split the predicates in `src/rif/rls.py`** — add write-side predicates and use them in `enable_statements`; extend the module docstring with one paragraph: reads require any membership, writes require a `MEMBER` membership, so the dormant `VIEWER` role is enforced by Postgres from day one.

```python
_WRITE_PREDICATE = (
    "space_id IN (SELECT space_id FROM memberships "
    "WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid "
    "AND role = 'MEMBER')"
)

_REVISION_WRITE_PREDICATE = (
    "page_id IN (SELECT p.id FROM pages p "
    "JOIN memberships m ON m.space_id = p.space_id "
    "WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid "
    "AND m.role = 'MEMBER')"
)
```

In `enable_statements()`:

```python
        statements.append(
            f"CREATE POLICY {table}_member ON {table} "
            f"USING ({_MEMBER_PREDICATE}) WITH CHECK ({_WRITE_PREDICATE})"
        )
```

and for revisions:

```python
    statements.append(
        "CREATE POLICY revisions_member ON revisions "
        f"USING ({_REVISION_PREDICATE}) WITH CHECK ({_REVISION_WRITE_PREDICATE})"
    )
```

- [ ] **Step 4: Full suite green**

Run: `uv run pytest -q`
Expected: PASS (conftest drops and rebuilds the schema per session, so the new policies apply; all existing writers have role MEMBER).

- [ ] **Step 5: Commit**

```bash
git add src/rif/rls.py tests/test_security.py
git commit -m "feat: role-aware RLS write predicate — viewers read-only at the database"
```

---

### Task 3: Slug addressing — resolve_space, space_alias, context, list_spaces, protocol

**Files:**
- Modify: `src/rif/access.py`
- Modify: `src/rif/context.py`
- Modify: `src/rif/protocol.py`
- Modify: `src/rif/server.py` (`tool_list_spaces` + `list_spaces` docstring)
- Modify: `tests/conftest.py` (fixture slug `school` → `household`)
- Test: `tests/test_access.py`, `tests/test_tools.py`, `tests/test_protocol.py`, `tests/test_context.py`

**Interfaces:**
- Produces: `resolve_space(session, principal, alias)` where `alias` is `"personal"` or a shared-space slug; `space_alias(space: Space) -> str` in `rif.access` returning `"personal"` for personal spaces, else `space.slug`; `tool_list_spaces` rows `{"name", "members", "you_are_owner", "version"}`.
- Consumes: Task 1 models.

**Note:** the conftest shared slug becomes `household`, so every existing test literal `"household"` keeps working — under slug semantics it now names the fixture's shared space. `promotion.py`'s hardcoded `"household"` literals also stay green against the fixture until Task 5 removes them.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_access.py` (it already has a `principal_for` helper or uses inline Principals; follow its existing conventions, adding the helper if absent):

```python
async def test_shared_space_resolves_by_slug(session, household):
    space = await resolve_space(
        session, principal_for(household["wouter"]), "household"
    )
    assert space.id == household["shared"].id


async def test_unknown_slug_and_foreign_slug_deny_identically(
    session, household, graph
):
    stranger = await graph.person("carla@example.test", "Carla")
    await graph.personal_space(stranger)
    with pytest.raises(AccessDenied) as missing:
        await resolve_space(session, principal_for(stranger), "no-such-space")
    with pytest.raises(AccessDenied) as foreign:
        await resolve_space(session, principal_for(stranger), "household")
    # same message shape: a slug probe cannot distinguish "absent" from "not yours"
    assert str(missing.value).replace("no-such-space", "household") == str(
        foreign.value
    )


async def test_one_person_in_two_shared_spaces(session, household, graph):
    trip = await graph.shared_space("trip", household["wouter"])
    a = await resolve_space(session, principal_for(household["wouter"]), "household")
    b = await resolve_space(session, principal_for(household["wouter"]), "trip")
    assert {a.id, b.id} == {household["shared"].id, trip.id}


async def test_space_alias_names_personal_and_slugs(household):
    from rif.access import space_alias

    assert space_alias(household["w_personal"]) == "personal"
    assert space_alias(household["shared"]) == "household"
```

- [ ] **Step 2: Change the fixture slug** — in `tests/conftest.py` `household` fixture: `shared = await graph.shared_space("household", wouter, partner)`.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_access.py -q`
Expected: FAIL — `space_alias` doesn't exist; slug `trip` doesn't resolve (`no unique ... space`).

- [ ] **Step 4: Rewrite `src/rif/access.py` resolution** — delete `_ALIASES`, replace `resolve_space`, add `space_alias`:

```python
def space_alias(space: Space) -> str:
    """Return the name a space goes by at the tool boundary.

    The principal's own personal space is always addressed as ``personal``;
    every shared space is addressed by its slug.

    :param space: the space to name
    :returns: ``personal`` or the space's slug
    """
    return "personal" if space.kind is SpaceKind.PERSONAL else space.slug


async def resolve_space(
    session: AsyncSession, principal: Principal, alias: str
) -> Space:
    """Resolve a space name for a principal, arming RLS as a side effect.

    ``personal`` resolves through ownership, not just membership, so
    malformed membership rows cannot hand someone another person's space.
    Any other name is a shared-space slug, resolved through membership. The
    denial message is identical for a missing slug and a slug the principal
    is not a member of, so probing cannot reveal which spaces exist.

    :param session: database session
    :param principal: the authenticated person
    :param alias: ``personal`` or a shared-space slug
    :raises AccessDenied: if no such space is reachable by this principal
    :returns: the resolved space
    """
    await _set_rls_principal(session, principal)
    stmt = (
        select(Space)
        .join(Membership, Membership.space_id == Space.id)
        .where(Membership.person_id == principal.person_id)
    )
    if alias == "personal":
        stmt = stmt.where(
            Space.kind == SpaceKind.PERSONAL,
            Space.owner_person_id == principal.person_id,
        )
    else:
        stmt = stmt.where(Space.kind == SpaceKind.SHARED, Space.slug == alias)
    space = await session.scalar(stmt)
    if space is None:
        raise AccessDenied(f"no space {alias!r} for {principal.email}")
    return space
```

`accessible_spaces` keeps `.order_by(Space.kind)` — Postgres orders enums by declaration order, so PERSONAL sorts first; fix its docstring to say so.

- [ ] **Step 5: Update `src/rif/context.py`** — delete `_ALIAS_BY_KIND`; import `space_alias` from `rif.access`; in `build_index` and `load_context` replace `_ALIAS_BY_KIND[space.kind]` with `space_alias(space)`; index version becomes:

```python
version = (
    ";".join(f"{space_alias(space)}={space.version}" for space in spaces) or "empty"
)
```

(`load_context`'s version already keys on `ctx.alias` — no change.)

- [ ] **Step 6: Update `src/rif/protocol.py`** — protocol now lives beside the persona:

```python
    protocol = await get_page(session, principal, "personal", PROTOCOL_PATH)
    persona = await get_page(session, principal, "personal", PERSONA_PATH)
```

Update `build_instructions`'s docstring (protocol is per-person, in the personal space). In `tests/test_protocol.py::test_concatenates_protocol_and_persona`, write the protocol page to `"personal"` instead of `"household"`.

- [ ] **Step 7: Update `tool_list_spaces` in `src/rif/server.py`** — remove the `_ALIAS_BY_KIND` import; new payload with members and ownership:

```python
async def tool_list_spaces(session: AsyncSession, principal: Principal) -> list[dict]:
    """List the principal's spaces with names, members, and ownership.

    Member display names are part of the payload on purpose: with open
    invites, knowing who is in the room is the informed-consent property,
    and it must be one call away.

    :param session: database session
    :param principal: the authenticated person
    :returns: one dict per accessible space
    """
    spaces = await accessible_spaces(session, principal)
    rows = (
        await session.execute(
            select(Membership.space_id, Person.display_name)
            .join(Person, Person.id == Membership.person_id)
            .where(Membership.space_id.in_([s.id for s in spaces]))
        )
    ).all()
    names: dict = {}
    for space_id, display_name in rows:
        names.setdefault(space_id, []).append(display_name)
    return [
        {
            "name": space_alias(s),
            "version": s.version,
            "members": sorted(names.get(s.id, [])),
            "you_are_owner": s.owner_person_id == principal.person_id,
        }
        for s in spaces
    ]
```

Imports: add `Membership, Person` to the `rif.models` import and `space_alias` to the `rif.access` import. Update the `list_spaces` MCP docstring to: "List your spaces: name, members, whether you own it, and a version counter."

- [ ] **Step 8: Rewrite the list_spaces test** — replace `tests/test_tools.py::test_tool_list_spaces_returns_alias_not_slug` with:

```python
async def test_tool_list_spaces_names_members_and_ownership(session, household):
    me = principal_for(household["wouter"])
    result = await tool_list_spaces(session, me)
    by_name = {row["name"]: row for row in result}
    assert set(by_name) == {"personal", "household"}
    assert by_name["personal"]["members"] == ["Wouter"]
    assert by_name["personal"]["you_are_owner"] is True
    assert by_name["household"]["members"] == ["Partner", "Wouter"]
    assert by_name["household"]["you_are_owner"] is True
    for row in result:
        assert set(row) == {"name", "members", "you_are_owner", "version"}
```

- [ ] **Step 9: Full suite green** — run `uv run pytest -q`. If `tests/test_context.py` asserts the old `alias`/version literals (e.g. a `household=` version fragment or `alias == "household"`), those now still pass because the fixture slug is `household`; fix any residual failures by updating expected literals only — semantics must not change.

- [ ] **Step 10: Commit**

```bash
git add src/rif/access.py src/rif/context.py src/rif/protocol.py src/rif/server.py tests/
git commit -m "feat: slug addressing — spaces are named groups; protocol moves to the personal space"
```

---

### Task 4: Space administration — create_space, invite, remove_member, onboarding helper

**Files:**
- Create: `src/rif/spaces.py`
- Modify: `src/rif/protocol.py` (templates)
- Test: `tests/test_spaces.py` (new)

**Interfaces:**
- Consumes: `resolve_space`, `save_page`, Task 1 models.
- Produces (for Tasks 6–7): in `rif.spaces` — `SpaceError`, `create_space(session, principal, slug) -> Space`, `invite(session, principal, slug, email, display_name=None) -> dict`, `remove_member(session, principal, slug, email) -> dict`, `ensure_personal_space(session, person) -> None`. In `rif.protocol` — `PROTOCOL_TEMPLATE: str`, `PERSONA_STUB: str`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_spaces.py`:

```python
import pytest
from sqlalchemy import select

from rif.access import AccessDenied, Principal, resolve_space
from rif.models import MemberRole, Membership, Person, Space, SpaceKind
from rif.pages import get_page, save_page
from rif.spaces import (
    SpaceError,
    create_space,
    ensure_personal_space,
    invite,
    remove_member,
)


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_create_space_makes_owner_the_first_member(session, household):
    me = principal_for(household["wouter"])
    space = await create_space(session, me, "trip")
    assert space.kind is SpaceKind.SHARED
    assert space.owner_person_id == household["wouter"].id
    assert (await resolve_space(session, me, "trip")).id == space.id


async def test_create_space_rejects_bad_and_taken_names(session, household):
    me = principal_for(household["wouter"])
    for bad in ("personal", "Has Caps", "-leading", "a", "household"):
        with pytest.raises(SpaceError):
            await create_space(session, me, bad)


async def test_invite_new_email_creates_person_and_membership(session, household):
    me = principal_for(household["wouter"])
    result = await invite(
        session, me, "household", "Anna@Example.test", display_name="Anna"
    )
    assert result["already_member"] is False
    anna = await session.scalar(
        select(Person).where(Person.email == "anna@example.test")
    )
    assert anna is not None and anna.subject is None
    assert anna.invited_by_person_id == household["wouter"].id
    row = await session.get(Membership, (anna.id, household["shared"].id))
    assert row is not None and row.role is MemberRole.MEMBER


async def test_invite_discloses_scope_and_is_idempotent(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "household", "a.md", "x", message="x")
    first = await invite(session, me, "household", "anna@example.test")
    again = await invite(session, me, "household", "anna@example.test")
    assert "permanently" in first["disclosure"] and "1 page" in first["disclosure"]
    assert again["already_member"] is True


async def test_only_the_owner_invites_or_removes(session, household):
    partner = principal_for(household["partner"])
    with pytest.raises(SpaceError):
        await invite(session, partner, "household", "anna@example.test")
    with pytest.raises(SpaceError):
        await remove_member(session, partner, "household", "wouter@example.test")


async def test_the_personal_space_cannot_be_shared(session, household):
    me = principal_for(household["wouter"])
    with pytest.raises(SpaceError):
        await invite(session, me, "personal", "anna@example.test")


async def test_remove_member_revokes_future_reads(session, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await remove_member(session, me, "household", "partner@example.test")
    with pytest.raises(AccessDenied):
        await resolve_space(session, theirs, "household")


async def test_owner_cannot_remove_themselves(session, household):
    me = principal_for(household["wouter"])
    with pytest.raises(SpaceError):
        await remove_member(session, me, "household", "wouter@example.test")


async def test_removing_unbound_invitee_erases_the_orphan_person(session, household):
    me = principal_for(household["wouter"])
    await invite(session, me, "household", "typo@example.test")
    result = await remove_member(session, me, "household", "typo@example.test")
    assert result["person_erased"] is True
    assert (
        await session.scalar(select(Person).where(Person.email == "typo@example.test"))
        is None
    )


async def test_ensure_personal_space_seeds_protocol_and_persona_once(session, graph):
    anna = await graph.person("anna@example.test", "Anna")
    await ensure_personal_space(session, anna)
    await ensure_personal_space(session, anna)  # idempotent
    me = principal_for(anna)
    protocol = await get_page(session, me, "personal", "meta/protocol.md")
    persona = await get_page(session, me, "personal", "meta/persona.md")
    assert protocol is not None and persona is not None
    assert protocol.version == 1  # seeded once, not twice
    spaces = await session.scalars(
        select(Space).where(Space.owner_person_id == anna.id)
    )
    assert len(list(spaces)) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_spaces.py -q`
Expected: FAIL — `rif.spaces` does not exist.

- [ ] **Step 3: Add templates to `src/rif/protocol.py`** — rename the `_FALLBACK` constant to `PROTOCOL_TEMPLATE` (same text, module-level, exported) and keep using it as the fallback in `build_instructions`; add:

```python
PERSONA_STUB = (
    "# Persona\n\nNot yet written. This is a first meeting: introduce "
    "yourself, ask what the user would like to call you, and interview "
    "gently to fill this page in."
)
```

- [ ] **Step 4: Implement `src/rif/spaces.py`**

```python
"""Space administration: creation, invitation, removal, and onboarding.

The ``spaces`` and ``memberships`` tables carry no RLS; every function here
is therefore an enforcement point and checks authority itself. The rule is
creator-admin: whoever created a space owns it, and only the owner changes
its member list.
"""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rif.access import Principal, resolve_space
from rif.models import Membership, Page, Person, Space, SpaceKind
from rif.pages import save_page
from rif.protocol import PERSONA_PATH, PERSONA_STUB, PROTOCOL_PATH, PROTOCOL_TEMPLATE

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_RESERVED = {"personal"}


class SpaceError(Exception):
    """Raised when a space-administration request cannot proceed."""


async def create_space(session: AsyncSession, principal: Principal, slug: str) -> Space:
    """Create a shared space; the creator becomes owner and first member.

    :param session: database session
    :param principal: the authenticated person
    :param slug: the space's name — lowercase letters, digits, hyphens
    :raises SpaceError: for an invalid, reserved, or already-taken name
    :returns: the created space
    """
    if slug in _RESERVED or not _SLUG_RE.match(slug):
        raise SpaceError(
            f"{slug!r} is not a usable space name: 2-64 characters, lowercase "
            "letters, digits, and hyphens, starting with a letter; "
            "'personal' is reserved"
        )
    if await session.scalar(select(Space).where(Space.slug == slug)) is not None:
        raise SpaceError(f"a space named {slug!r} already exists; pick another name")
    space = Space(slug=slug, kind=SpaceKind.SHARED, owner_person_id=principal.person_id)
    session.add(space)
    await session.flush()
    session.add(Membership(person_id=principal.person_id, space_id=space.id))
    await session.flush()
    return space


async def _owned_shared_space(
    session: AsyncSession, principal: Principal, slug: str
) -> Space:
    """Resolve ``slug`` and require it to be a shared space this principal owns.

    :param session: database session
    :param principal: the authenticated person
    :param slug: the space name as given by the caller
    :raises SpaceError: if the space is personal or owned by someone else
    :returns: the resolved space
    """
    space = await resolve_space(session, principal, slug)
    if space.kind is SpaceKind.PERSONAL:
        raise SpaceError("the personal space cannot be shared or administered")
    if space.owner_person_id != principal.person_id:
        raise SpaceError(f"only the owner of {slug!r} may change its members")
    return space


async def invite(
    session: AsyncSession,
    principal: Principal,
    slug: str,
    email: str,
    display_name: str | None = None,
) -> dict:
    """Invite an email address into a shared space the principal owns.

    An unknown email becomes a person row on the spot — the runtime
    allowlist entry. The invitee gets in when they first sign in with this
    email, verified, through the unchanged binding path in ``rif.auth``.

    :param session: database session
    :param principal: the authenticated person
    :param slug: the shared space to invite into
    :param email: the address the invitee will sign in with
    :param display_name: how members see them; defaults to the email's name part
    :raises SpaceError: if the principal does not own the space
    :returns: outcome with the disclosure text and an ``already_member`` flag
    """
    space = await _owned_shared_space(session, principal, slug)
    email = email.strip().lower()
    person = await session.scalar(select(Person).where(Person.email == email))
    if person is None:
        person = Person(
            email=email,
            display_name=display_name or email.split("@")[0],
            invited_by_person_id=principal.person_id,
        )
        session.add(person)
        await session.flush()
    membership = await session.get(Membership, (person.id, space.id))
    already = membership is not None
    if not already:
        session.add(Membership(person_id=person.id, space_id=space.id))
        await session.flush()
    page_count = await session.scalar(
        select(func.count()).select_from(Page).where(Page.space_id == space.id)
    )
    return {
        "space": slug,
        "email": email,
        "already_member": already,
        "disclosure": (
            f"{email} will permanently see everything in {slug!r}, past and "
            f"future — {page_count} page(s) today. There is no un-sharing "
            "what they read."
        ),
    }


async def remove_member(
    session: AsyncSession, principal: Principal, slug: str, email: str
) -> dict:
    """Remove a member from a shared space the principal owns.

    Removal stops future access; it cannot unshare what was already read.
    Removing an invitee who never signed in (no bound subject, no other
    memberships) also erases the orphaned person row — the typo-repair path.

    :param session: database session
    :param principal: the authenticated person
    :param slug: the shared space to remove from
    :param email: the member's email
    :raises SpaceError: if not owner, target absent, or target is the owner
    :returns: outcome with a ``person_erased`` flag
    """
    space = await _owned_shared_space(session, principal, slug)
    email = email.strip().lower()
    person = await session.scalar(select(Person).where(Person.email == email))
    membership = (
        None if person is None else await session.get(Membership, (person.id, space.id))
    )
    if membership is None:
        raise SpaceError(f"{email} is not a member of {slug!r}")
    if person.id == principal.person_id:
        raise SpaceError("the owner cannot remove themselves from their own space")
    await session.delete(membership)
    await session.flush()
    person_erased = False
    if person.subject is None:
        remaining = await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.person_id == person.id)
        )
        if remaining == 0:
            await session.delete(person)
            await session.flush()
            person_erased = True
    return {
        "space": slug,
        "email": email,
        "removed": True,
        "person_erased": person_erased,
    }


async def ensure_personal_space(session: AsyncSession, person: Person) -> None:
    """Create the person's personal space and starter pages, once.

    Called at first sign-in. The slug is derived from the person id — it is
    globally unique by construction and never crosses the tool boundary,
    because personal spaces are always addressed by the ``personal`` alias.

    :param session: database session
    :param person: the newly bound person
    """
    existing = await session.scalar(
        select(Space).where(
            Space.kind == SpaceKind.PERSONAL, Space.owner_person_id == person.id
        )
    )
    if existing is not None:
        return
    space = Space(
        slug=f"personal-{person.id.hex}",
        kind=SpaceKind.PERSONAL,
        owner_person_id=person.id,
    )
    session.add(space)
    await session.flush()
    session.add(Membership(person_id=person.id, space_id=space.id))
    await session.flush()
    principal = Principal(person_id=person.id, email=person.email)
    await save_page(
        session,
        principal,
        "personal",
        PROTOCOL_PATH,
        PROTOCOL_TEMPLATE,
        message="seeded at first sign-in",
        title="Operating protocol",
        allow_protected=True,
    )
    await save_page(
        session,
        principal,
        "personal",
        PERSONA_PATH,
        PERSONA_STUB,
        message="seeded at first sign-in",
        title="Persona",
        allow_protected=True,
    )
```

- [ ] **Step 5: Run the new tests, then the full suite**

Run: `uv run pytest tests/test_spaces.py -q && uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/rif/spaces.py src/rif/protocol.py tests/test_spaces.py
git commit -m "feat: space administration — create_space, email-bound invite, remove_member, onboarding"
```

---

### Task 5: Sharing to any space — promotion rework

**Files:**
- Modify: `src/rif/models.py` (`Promotion.dest_space_id`)
- Modify: `src/rif/promotion.py`
- Modify: `src/rif/server.py` (`prepare_to_share` signature + docstring)
- Test: `tests/test_promotion.py`

**Interfaces:**
- Produces: `prepare_promotion(session, principal, path, dest_space, *, section=None, dest_path=None) -> dict` (result gains `"dest_space"` and `"members"`); `Promotion.dest_space_id: Mapped[UUID]`.
- Consumes: `resolve_space` slugs (Task 3), `space_alias` not needed here.

- [ ] **Step 1: Add the column** — in `src/rif/models.py` `Promotion`, after `source_version`:

```python
    dest_space_id: Mapped[UUID] = mapped_column(ForeignKey("spaces.id"))
```

- [ ] **Step 2: Write/adjust the failing tests** — in `tests/test_promotion.py`:

Every `prepare_promotion(session, me, <path>, ...)` call gains the destination argument `"household"` right after the path, e.g. `prepare_promotion(session, me, "boiler.md", "household")` and `prepare_promotion(session, me, "house-notes.md", "household", section=SECTION, dest_path="boiler.md")`. Then append the new behavior tests:

```python
async def test_share_targets_a_chosen_space(session, household, graph):
    me = principal_for(household["wouter"])
    trip = await graph.shared_space("trip", household["wouter"])
    await save_page(session, me, "personal", "packing.md", "tent, stove", message="x")
    prepared = await prepare_promotion(session, me, "packing.md", "trip")
    assert prepared["dest_space"] == "trip"
    await confirm_promotion(session, me, prepared["nonce"])
    assert (await get_page(session, me, "trip", "packing.md")).body == "tent, stove"
    assert await get_page(session, me, "household", "packing.md") is None


async def test_disclosure_enumerates_the_destination_members(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "m.md", "x", message="x")
    prepared = await prepare_promotion(session, me, "m.md", "household")
    assert sorted(prepared["members"]) == ["Partner", "Wouter"]
    assert "Partner" in prepared["warning"]


async def test_share_to_unjoined_or_personal_space_is_refused(
    session, household, graph
):
    me = principal_for(household["wouter"])
    stranger = await graph.person("carla@example.test", "Carla")
    await graph.shared_space("carla-club", stranger)
    await save_page(session, me, "personal", "n.md", "x", message="x")
    with pytest.raises(PromotionError):
        await prepare_promotion(session, me, "n.md", "carla-club")
    with pytest.raises(PromotionError):
        await prepare_promotion(session, me, "n.md", "personal")
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_promotion.py -q`
Expected: FAIL — `prepare_promotion` takes no destination argument.

- [ ] **Step 4: Rework `src/rif/promotion.py`**

`prepare_promotion` — new signature and destination handling (docstring updated to match; imports gain `AccessDenied`, `Membership`, `Person`, `Space`):

```python
async def prepare_promotion(
    session: AsyncSession,
    principal: Principal,
    path: str,
    dest_space: str,
    *,
    section: str | None = None,
    dest_path: str | None = None,
) -> dict:
```

Body changes, in order:

```python
if dest_space == "personal":
    raise PromotionError(
        "sharing moves content out of the personal space; pick a shared "
        "space from list_spaces as the destination"
    )
try:
    dest = await resolve_space(session, principal, dest_space)
except AccessDenied as exc:
    raise PromotionError(str(exc)) from exc
page = await get_page(session, principal, "personal", path)
if page is None:
    raise PromotionError(f"no personal page at {path!r}")
# ... section validation unchanged ...
staged = Promotion(
    person_id=principal.person_id,
    source_page_id=page.id,
    source_version=page.version,
    dest_space_id=dest.id,
    dest_path=dest_path or path,
    section_text=section,
)
session.add(staged)
await session.flush()
members = sorted(
    (
        await session.scalars(
            select(Person.display_name)
            .join(Membership, Membership.person_id == Person.id)
            .where(Membership.space_id == dest.id)
        )
    ).all()
)
return {
    "nonce": str(staged.id),
    "dest_space": dest_space,
    "dest_path": staged.dest_path,
    "members": members,
    "disclosure": section if section is not None else page.body,
    "warning": (
        f"Sharing is permanent; there is no un-sharing. Everyone in "
        f"{dest_space!r} — {', '.join(members)} — and anyone invited "
        "later can read this forever."
    ),
}
```

`confirm_promotion` — resolve the staged destination and replace every `"household"` literal. Order inside the function:

1. Right after the nonce-ownership check, load the destination:
   `dest = await session.get(Space, staged.dest_space_id)`.
2. The idempotent-retry return (consumed nonce) gains `"dest_space": dest.slug`.
3. After the TTL and source-version checks, verify the sharer is still a member:

```python
    try:
        await resolve_space(session, principal, dest.slug)
    except AccessDenied as exc:
        raise PromotionError(str(exc)) from exc
```

4. Use `dest.slug` where `"household"` appeared: the destination-exists check (`get_page(session, principal, dest.slug, staged.dest_path)`), both `save_page` destination calls, and the human-facing strings — the section marker becomes `f"*(section moved to the {dest.slug} space — see {staged.dest_path} there)*"` (keep the original's backtick styling around the path), the stub body becomes `f"# {source.title}\n\nMoved to the {dest.slug} space; see {staged.dest_path} there."`, and the revision messages keep their current form. The final return gains `"dest_space": dest.slug`. Update both docstrings: destination is the staged space, not "household".

- [ ] **Step 5: Update the MCP tool in `src/rif/server.py`**

```python
@mcp.tool
async def prepare_to_share(
    path: str, dest_space: str, section: str | None = None, dest_path: str | None = None
) -> dict:
    """Stage sharing a personal page — or one section — into a shared space.

    Step 1 of 2. Whole page: pass path and dest_space. One section: also
    pass the exact text to extract as section, and name the new page it
    becomes with dest_path — the rest of the page stays private, and the
    extracted text must make sense on its own.

    Show the user the returned disclosure, members, and warning, and only
    call confirm_share after they explicitly agree in this conversation.
    Sharing is permanent: every member of the destination space — current
    and future — can then read the content forever.

    :param path: page path in the personal space
    :param dest_space: destination space name, from list_spaces
    :param section: exact span to extract; omit to share the whole page
    :param dest_path: name for the extracted page; required with section
    """
    async with session_scope() as session:
        principal = await current_principal(session)
        try:
            return await prepare_promotion(
                session,
                principal,
                path,
                dest_space,
                section=section,
                dest_path=dest_path,
            )
        except PromotionError as exc:
            return {"error": "promotion_failed", "detail": str(exc)}
```

- [ ] **Step 6: Full suite green**

Run: `uv run pytest -q`
Expected: PASS (fixture slug `household` satisfies all adjusted literals; stub-assertion `"household" in stub.body.lower()` still holds because the marker names the slug).

- [ ] **Step 7: Commit**

```bash
git add src/rif/models.py src/rif/promotion.py src/rif/server.py tests/test_promotion.py
git commit -m "feat: sharing targets any shared space; disclosure names the audience"
```

---

### Task 6: Onboarding at first sign-in

**Files:**
- Modify: `src/rif/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `ensure_personal_space` (Task 4), `invite` (Task 4).

- [ ] **Step 1: Write the failing test** — append to `tests/test_auth.py`:

```python
async def test_first_bind_onboards_a_personal_space(session, household):
    from rif.access import Principal
    from rif.pages import get_page
    from rif.spaces import invite

    owner = Principal(person_id=household["wouter"].id, email=household["wouter"].email)
    await invite(session, owner, "household", "anna@example.test", display_name="Anna")
    claims = {"sub": "auth0|anna", "email": "anna@example.test", "email_verified": True}
    principal = await principal_from_claims(session, claims)
    protocol = await get_page(session, principal, "personal", "meta/protocol.md")
    persona = await get_page(session, principal, "personal", "meta/persona.md")
    assert protocol is not None and persona is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auth.py -q`
Expected: FAIL — no personal space exists for Anna, so `get_page(..., "personal", ...)` raises `AccessDenied`.

- [ ] **Step 3: Hook onboarding into `src/rif/auth.py`** — in `principal_from_claims`, after `person.subject = subject` / `await session.flush()`:

```python
        person.subject = subject
        await session.flush()
        await ensure_personal_space(session, person)
```

with `from rif.spaces import ensure_personal_space` at module level. Rewrite the docstring's allowlist paragraph to the new contract:

```
    The persons table is still the gate, but its rows are now created by
    invitation at runtime, not by migration. An unknown identity is denied
    exactly as before: a token whose email no member ever invited never gets
    in — invitation-only, never open signup. First sign-in binds the
    provider subject and onboards a personal space with starter pages.
```

- [ ] **Step 4: Full suite green**

Run: `uv run pytest -q`
Expected: PASS — `test_stranger_is_denied` still passes (uninvited emails have no person row); seeded persons already own personal spaces, so `ensure_personal_space` is a no-op for them.

- [ ] **Step 5: Commit**

```bash
git add src/rif/auth.py tests/test_auth.py
git commit -m "feat: first sign-in onboards a personal space with starter pages"
```

---

### Task 7: MCP tool surface — create_space, invite, remove_member, wording

**Files:**
- Modify: `src/rif/server.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: MCP tools `create_space(slug)`, `invite(space, email, display_name=None)`, `remove_member(space, email)`; testable helpers `tool_create_space`, `tool_invite`, `tool_remove_member`.
- Consumes: `rif.spaces` (Task 4).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_tools.py`:

```python
async def test_tool_create_space_and_error_mapping(session, household):
    from rif.server import tool_create_space

    me = principal_for(household["wouter"])
    created = await tool_create_space(session, me, "trip")
    assert created == {"name": "trip", "members": ["Wouter"], "you_are_owner": True}
    taken = await tool_create_space(session, me, "trip")
    assert taken["error"] == "space_error"


async def test_tool_invite_and_remove_round_trip(session, household):
    from rif.server import tool_invite, tool_remove_member

    me = principal_for(household["wouter"])
    invited = await tool_invite(
        session, me, "household", "anna@example.test", display_name="Anna"
    )
    assert invited["already_member"] is False and "permanently" in invited["disclosure"]
    removed = await tool_remove_member(session, me, "household", "anna@example.test")
    assert removed["removed"] is True and removed["person_erased"] is True
    not_owner = await tool_invite(
        session, principal_for(household["partner"]), "household", "x@example.test"
    )
    assert not_owner["error"] == "space_error"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tools.py -q`
Expected: FAIL — the helpers don't exist.

- [ ] **Step 3: Implement in `src/rif/server.py`** — import the module (`from rif import spaces as space_admin` and `from rif.spaces import SpaceError`), add helpers + tools following the existing split pattern:

```python
async def _member_names(session: AsyncSession, space_id) -> list[str]:
    """Return the sorted display names of a space's members.

    :param session: database session
    :param space_id: the space to list
    :returns: display names, sorted
    """
    return sorted(
        (
            await session.scalars(
                select(Person.display_name)
                .join(Membership, Membership.person_id == Person.id)
                .where(Membership.space_id == space_id)
            )
        ).all()
    )


async def tool_create_space(
    session: AsyncSession, principal: Principal, slug: str
) -> dict:
    """Create a shared space; split from the tool for testability.

    :param session: database session
    :param principal: the authenticated person
    :param slug: the new space's name
    :returns: name, members, ownership — or an error dict
    """
    try:
        space = await space_admin.create_space(session, principal, slug)
    except SpaceError as exc:
        return {"error": "space_error", "detail": str(exc)}
    return {
        "name": space.slug,
        "members": await _member_names(session, space.id),
        "you_are_owner": True,
    }


async def tool_invite(
    session: AsyncSession,
    principal: Principal,
    space: str,
    email: str,
    display_name: str | None = None,
) -> dict:
    """Invite an email into a space; split from the tool for testability.

    :param session: database session
    :param principal: the authenticated person
    :param space: the shared space name
    :param email: the invitee's sign-in email
    :param display_name: how members will see them
    :returns: the invite outcome with disclosure, or an error dict
    """
    try:
        return await space_admin.invite(
            session, principal, space, email, display_name=display_name
        )
    except (SpaceError, AccessDenied) as exc:
        return {"error": "space_error", "detail": str(exc)}


async def tool_remove_member(
    session: AsyncSession, principal: Principal, space: str, email: str
) -> dict:
    """Remove a member from a space; split from the tool for testability.

    :param session: database session
    :param principal: the authenticated person
    :param space: the shared space name
    :param email: the member's email
    :returns: the removal outcome, or an error dict
    """
    try:
        return await space_admin.remove_member(session, principal, space, email)
    except (SpaceError, AccessDenied) as exc:
        return {"error": "space_error", "detail": str(exc)}
```

(`AccessDenied` joins the `rif.access` import; `tool_list_spaces` from Task 3 can now also use `_member_names` — refactor it to do so.) Then the MCP tools:

```python
@mcp.tool
async def create_space(slug: str) -> dict:
    """Create a new shared space that you own.

    You become the only member; use invite to bring people in. Names are
    lowercase letters, digits, and hyphens, like "school" or "trip-2027".

    :param slug: the space's name
    """
    async with session_scope() as session:
        principal = await current_principal(session)
        return await tool_create_space(session, principal, slug)


@mcp.tool
async def invite(space: str, email: str, display_name: str | None = None) -> dict:
    """Invite a person into a shared space you own. Owner only.

    Tell the user exactly what this grants before calling: the invitee will
    permanently see everything in the space, past and future. They get in by
    signing in with this exact email address, verified.

    :param space: the space name, from list_spaces
    :param email: the address the invitee will sign in with
    :param display_name: how members will see them
    """
    async with session_scope() as session:
        principal = await current_principal(session)
        return await tool_invite(
            session, principal, space, email, display_name=display_name
        )


@mcp.tool
async def remove_member(space: str, email: str) -> dict:
    """Remove a member from a shared space you own. Owner only.

    Removal stops future access. It cannot unshare what they already read.

    :param space: the space name, from list_spaces
    :param email: the member's email
    """
    async with session_scope() as session:
        principal = await current_principal(session)
        return await tool_remove_member(session, principal, space, email)
```

- [ ] **Step 4: Wording sweep in `src/rif/server.py`** — mechanical, no logic:
  - FastMCP `instructions`: replace the first sentence with `"Long-term memory shared between you and the people in your spaces."` (rest unchanged).
  - Every `:param space:` docstring line reading ```` ``personal`` or ``household`` ```` becomes ```` ``personal`` or a space name from list_spaces ```` (tools: `read_pages`, `read_page`, `remember`, `write_page`, `edit_page_section`, `update_meta_page`, `add_image`, `read_image`; helpers `tool_read_pages`, `tool_read_page`, `tool_remember`).
  - `remember`'s tool docstring middle line becomes: `"Only pass a space name when the fact clearly concerns that group — a jointly-owned thing, a joint decision, a shared obligation. Anything ambiguous is personal."`

- [ ] **Step 5: Full suite green**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/rif/server.py tests/test_tools.py
git commit -m "feat: create_space, invite, and remove_member cross the tool boundary"
```

---

### Task 8: Schema migration

**Files:**
- Create: `migrations/versions/7c4d1a9e2b58_multi_user_spaces.py` — **as ported:**
  `src/rif/piccolo_migrations/rif_2026_08_08t10_00_00_000000.py`, migration id
  `2026-08-08T10:00:00:000000`, description `multi-user spaces`.

**Interfaces:**
- Consumes: `rif.rls.disable_statements`/`enable_statements` (Task 2 versions), final model shape from Tasks 1 and 5.

**Two differences the Piccolo migration had to make**, both recorded in its own
docstring: `spaces.kind` is a plain `VARCHAR` (Piccolo validates `choices` in
Python), so `'household'` → `'shared'` is an `UPDATE`, not the enum-value
rename below; and `disable_statements()` runs *before* the data moves, not
after, because `promotions` carries `FORCE ROW LEVEL SECURITY` and the
migration role is the table owner in local development — with the policy live
the `dest_space_id` backfill reaches zero rows and the following `SET NOT NULL`
fails. Alembic never hit that: it ran as a superuser.

- [ ] **Step 1: Write the migration**

```python
"""multi-user spaces: shared kind, member roles, owned spaces, dest space

Revision ID: 7c4d1a9e2b58
Revises: 131e9ad66476
Create Date: 2026-08-07

Owner backfill picks the member with the lowest person id for any shared
space that predates ownership. That choice is arbitrary but deterministic;
the runbook tells the operator how to verify and, if needed, reassign with
a single UPDATE before anyone relies on owner-only administration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from rif.rls import disable_statements, enable_statements

revision: str = "7c4d1a9e2b58"
down_revision: str | Sequence[str] | None = "131e9ad66476"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE spacekind RENAME VALUE 'HOUSEHOLD' TO 'SHARED'")

    sa.Enum("MEMBER", "VIEWER", name="memberrole").create(op.get_bind())
    op.add_column(
        "memberships",
        sa.Column(
            "role",
            sa.Enum("MEMBER", "VIEWER", name="memberrole"),
            nullable=False,
            server_default="MEMBER",
        ),
    )

    op.add_column(
        "persons", sa.Column("invited_by_person_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "persons_invited_by_person_id_fkey",
        "persons",
        "persons",
        ["invited_by_person_id"],
        ["id"],
    )
    op.add_column(
        "persons",
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
    )

    op.add_column("promotions", sa.Column("dest_space_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE promotions SET dest_space_id = "
        "(SELECT id FROM spaces WHERE kind = 'SHARED' ORDER BY slug LIMIT 1)"
    )
    op.alter_column("promotions", "dest_space_id", nullable=False)
    op.create_foreign_key(
        "promotions_dest_space_id_fkey",
        "promotions",
        "spaces",
        ["dest_space_id"],
        ["id"],
    )

    op.execute(
        "UPDATE spaces s SET owner_person_id = "
        "(SELECT m.person_id FROM memberships m WHERE m.space_id = s.id "
        " ORDER BY m.person_id LIMIT 1) "
        "WHERE s.owner_person_id IS NULL"
    )
    op.drop_constraint("spaces_owner_person_id_key", "spaces", type_="unique")
    op.create_index(
        "uq_personal_owner_person",
        "spaces",
        ["owner_person_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'PERSONAL'"),
    )
    op.alter_column("spaces", "owner_person_id", nullable=False)

    for statement in disable_statements():
        op.execute(statement)
    for statement in enable_statements():
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    for statement in disable_statements():
        op.execute(statement)
    op.alter_column("spaces", "owner_person_id", nullable=True)
    op.drop_index("uq_personal_owner_person", table_name="spaces")
    op.create_unique_constraint(
        "spaces_owner_person_id_key", "spaces", ["owner_person_id"]
    )
    op.drop_constraint(
        "promotions_dest_space_id_fkey", "promotions", type_="foreignkey"
    )
    op.drop_column("promotions", "dest_space_id")
    op.drop_column("persons", "created_at")
    op.drop_constraint(
        "persons_invited_by_person_id_fkey", "persons", type_="foreignkey"
    )
    op.drop_column("persons", "invited_by_person_id")
    op.drop_column("memberships", "role")
    sa.Enum(name="memberrole").drop(op.get_bind())
    op.execute("ALTER TYPE spacekind RENAME VALUE 'SHARED' TO 'HOUSEHOLD'")
    for statement in enable_statements():
        op.execute(statement)
```

Note the downgrade re-applies the *current* (role-aware) policies; that is fine — `disable_statements` at the top of any later upgrade clears them, and a true rollback to pre-role predicates would also require the old `rif.rls`, which git provides.

- [ ] **Step 2: Prove the whole chain runs on a fresh database**

Never against `rif` or `rif_test`. As ported (Piccolo):

```bash
docker compose up -d
docker compose exec db psql -U postgres -c "DROP DATABASE IF EXISTS rif_migrate_piccolo"
docker compose exec db psql -U postgres -c "CREATE DATABASE rif_migrate_piccolo OWNER rif"
DATABASE_URL="postgresql://rif:rif@localhost:5433/rif_migrate_piccolo" uv run piccolo migrations forwards rif
```

Expected: all five migrations apply cleanly. Then verify the shape:

```bash
docker compose exec db psql -U postgres -d rif_migrate_piccolo -c "SELECT slug, kind, owner_person_id IS NOT NULL AS owned FROM spaces ORDER BY slug"
docker compose exec db psql -U postgres -d rif_migrate_piccolo -c "SELECT role, count(*) FROM memberships GROUP BY role"
docker compose exec db psql -U postgres -d rif_migrate_piccolo -c "SELECT tablename, policyname, cmd FROM pg_policies WHERE schemaname='public' ORDER BY 1,2"
```

Expected: every space `owned = t`, kinds `personal`/`shared` (lowercase — the
values Piccolo stores); all memberships `member`; twelve per-command policies
across `pages`/`attachments`/`revisions` plus `promotions_owner`, and no
`{table}_member` `FOR ALL` policy left over.

The reverse is not exercised: `backwards()` refuses by design, because the
owner backfill and the kind rename are lossy and the live predicates need
`memberships.role`. Restoring from a backup is the supported path
(`docs/restore.md`).

- [ ] **Step 3: Full suite green** (unchanged by migrations, but confirm)

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/rif/piccolo_migrations/rif_2026_08_08t10_00_00_000000.py
git commit -m "feat: piccolo migration for multi-user spaces"
```

---

### Task 9: Docs, wording debt, and final verification

**Files:**
- Modify: `docs/spec.md`, `docs/how-it-works.html`, `docs/runbook.md`, `README.md`
- Modify: `src/rif/pages.py`, `src/rif/export.py` (docstrings only)
- Reference: `docs/superpowers/specs/2026-08-07-multi-user-spaces-design.md`

- [ ] **Step 1: Docstring debt** — in `src/rif/pages.py` and `src/rif/export.py`, update every `:param alias:`/`:param space:` line and module prose that says ```` ``personal`` or ``household`` ```` to ```` ``personal`` or a shared-space slug ````. No logic changes. (`export.py` already works with slugs — the alias flows straight into `resolve_space`.)

- [ ] **Step 2: `docs/spec.md`** — add a short revision note under the header: "Rev 4, 7 Aug 2026: spaces generalized from household tiers to named groups with email-bound invites — see `docs/superpowers/specs/2026-08-07-multi-user-spaces-design.md`, which supersedes this document's 'two people, three spaces' framing, the closed-allowlist wording in the access-control section, and the 'more than two people' out-of-scope line."

- [ ] **Step 3: `docs/how-it-works.html`** (plain language, ISO 24495-1) — update the passages that state the fixed topology:
  - The "Three spaces, and who can enter" section (~lines 242–286): reframe as "Your space, and the spaces you share" — a space is a named group of people; the membership table records who may enter which space; every reader's access is a row in that table. Remove "It has four rows" / "Four membership rows, ever" and the two-missing-rows framing; replace with: the privacy model is that a space's pages are readable by exactly its members, and by no one else — enforced by the database, not by application code. Update the accompanying SVG labels the same way (household → the shared space's name; keep the diagram's two-person example but caption it as an example, not the whole world).
  - The `list_spaces` line (~line 500): "What you can see: your personal space and every shared space you belong to — with each space's member list, because knowing who is in the room is what informed sharing means."
  - The sharing section (~lines 380–392): add one sentence — the disclosure now names every member of the destination space before you confirm.
  - Add a short "Joining" paragraph: the owner of a space invites an email address; the invitee signs in with that address (verified) and is onboarded with a private personal space automatically. No invite links, no open signup.
- [ ] **Step 4: `docs/runbook.md`** — replace the hand-seeded allowlist steps ("the only two people who can ever get in", `list_spaces` returns exactly `personal` + `household`) with: persons are created by the invite tool; go-live check becomes "`list_spaces` returns that person's `personal` plus every shared space they were invited to". Add a post-migration step: verify each shared space's owner (`SELECT slug, p.email FROM spaces s JOIN persons p ON p.id = s.owner_person_id WHERE s.kind = 'SHARED'`) and reassign with `UPDATE spaces SET owner_person_id = (SELECT id FROM persons WHERE email = '<the-owner>') WHERE slug = '<the-space>'` if the arbitrary backfill picked the wrong member.

- [ ] **Step 5: `README.md`** — update the framing sentence from "more than one person in a household" to groups: rif is long-term memory shared between people through named spaces; a household is one such group.

- [ ] **Step 6: Full verification**

Run: `uv run pytest -q`
Expected: PASS, no skips introduced by this work.

- [ ] **Step 7: Commit**

```bash
git add docs/ README.md src/rif/pages.py src/rif/export.py
git commit -m "docs: spaces are named groups — spec revision, explainer, runbook, wording debt"
```

---

## Post-plan gates (not tasks — session-level)

1. **Security review:** run the `paranoid-security-auditor` agent over the invite/bind/onboard path (`src/rif/spaces.py`, `src/rif/auth.py`, `src/rif/rls.py`, the new server tools) before merge — the spec commits to this.
2. **Code review:** `superpowers:requesting-code-review` / `pr-review-toolkit` per the finishing flow.
