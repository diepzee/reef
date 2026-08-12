"""Adversarial tests against the RLS boundary itself.

These attack the database directly as the wrong principal and as no
principal, not just through the tool surface -- the boundary has to hold
against code that forgot to filter, which is the whole reason it is in
Postgres rather than in Python.
"""

import asyncio

import pytest

from rif.access import Principal, arm, resolve_space
from rif.db import transaction_scope
from rif.models import (
    Attachment,
    AttachmentStatus,
    MemberRole,
    Page,
    Person,
    Promotion,
    Revision,
)


def principal_for(person) -> Principal:
    """Build a principal from a seeded person row.

    :param person: a Person row
    :returns: the matching principal
    """
    return Principal(person_id=person.id, email=person.email)


async def _seed_private_page(household) -> Page:
    """Write one page into Wouter's personal space, as Wouter.

    FORCE ROW LEVEL SECURITY applies to the table owner too, so there is no
    unarmed path to seed through -- the row has to be written by its owner.

    :param household: the seeded household fixture
    :returns: the page that was written
    """
    principal = principal_for(household["wouter"])
    await resolve_space(principal, "personal")
    page = Page(
        space_id=household["w_personal"].id,
        path="health.md",
        title="Health",
        tags=[],
        body="private medical detail",
    )
    await page.save()
    await Revision(
        page_id=page.id,
        path=page.path,
        title=page.title,
        tags=[],
        body=page.body,
        message="seed",
        author_id=household["wouter"].id,
    ).save()
    return page


async def _seed_shared_page(household) -> Page:
    """Write one page into the shared space, as Wouter (a MEMBER).

    :param household: the seeded household fixture
    :returns: the page that was written
    """
    await resolve_space(principal_for(household["wouter"]), "personal")  # arms RLS
    page = Page(
        space_id=household["shared"].id,
        path="joint.md",
        title="Joint",
        tags=[],
        body="shared detail",
    )
    await page.save()
    return page


async def _seed_shared_content(household) -> tuple[Page, Revision, Attachment]:
    """Seed one page, one revision, and one attachment in the shared space.

    :param household: the seeded household fixture
    :returns: the seeded page, revision, and attachment
    """
    page = await _seed_shared_page(household)
    revision = Revision(
        page_id=page.id,
        path=page.path,
        title=page.title,
        tags=[],
        body=page.body,
        message="seed",
        author_id=household["wouter"].id,
    )
    await revision.save()
    attachment = Attachment(
        space_id=household["shared"].id,
        page_id=page.id,
        object_key="shared/joint.png",
        mime="image/png",
        byte_size=1,
        description="a photo",
        status=AttachmentStatus.READY.value,
    )
    await attachment.save()
    return page, revision, attachment


async def _admit_viewer(household, graph) -> Person:
    """Hand-insert a VIEWER membership into the shared space.

    Nothing in the application creates viewers yet, so the row is written
    directly -- the point is to prove Postgres already enforces the role.

    :param household: the seeded household fixture
    :param graph: the topology builder fixture
    :returns: the viewer person
    """
    anna = await graph.person("anna@example.test", "Anna")
    await graph.personal_space(anna)
    # Seeded: memberships now carries policies, and admitting somebody to a
    # cove is an owner-only act. This test is about what a viewer may do once
    # they are in, not about how they got there.
    await graph.add_membership(anna, household["shared"], MemberRole.VIEWER.value)
    return anna


async def _row_count(table: str, row_id) -> int:
    """Return how many rows of ``table`` with ``row_id`` the caller may read.

    :param table: content table name
    :param row_id: the row's primary key
    :returns: 1 if visible under the current principal, 0 if filtered out
    """
    rows = await Page.raw(f"SELECT count(*) AS n FROM {table} WHERE id = {{}}", row_id)
    return rows[0]["n"]


async def test_raw_select_as_other_principal_returns_nothing(household):
    """The partner must not see Wouter's private page, filtering or not."""
    async with transaction_scope():
        await _seed_private_page(household)
    async with transaction_scope():
        await arm(principal_for(household["partner"]))
        assert await Page.objects().where(Page.path == "health.md") == []


async def test_raw_select_on_revisions_is_also_denied(household):
    """History leaks as badly as the page; revisions carry their own policy."""
    async with transaction_scope():
        await _seed_private_page(household)
    async with transaction_scope():
        await arm(principal_for(household["partner"]))
        assert await Revision.objects() == []


async def test_forged_insert_into_foreign_space_is_rejected(household):
    """WITH CHECK must refuse a write aimed at a space you cannot see."""
    with pytest.raises(Exception) as exc:
        async with transaction_scope():
            await arm(principal_for(household["partner"]))
            await Page(
                space_id=household["w_personal"].id,
                path="planted.md",
                title="x",
                tags=[],
                body="forged",
            ).save()
    assert "policy" in str(exc.value).lower()


async def _stage_promotion(household) -> Promotion:
    """Stage one share nonce as Wouter, carrying a private section.

    :param household: the seeded household fixture
    :returns: the staged promotion row
    """
    page = await _seed_private_page(household)
    staged = Promotion(
        person_id=household["wouter"].id,
        source_page_id=page.id,
        source_version=page.version,
        dest_space_id=household["shared"].id,
        dest_path="shared.md",
        section_text="private medical detail",
    )
    await staged.save()
    return staged


async def test_promotion_nonce_is_invisible_to_the_other_principal(household):
    """A staged share must not be readable by anyone but its owner.

    ``section_text`` holds the exact span extracted from a personal page, so
    a readable promotion row is a readable private paragraph -- before the
    owner has agreed to disclose anything.
    """
    async with transaction_scope():
        await _stage_promotion(household)
    async with transaction_scope():
        await arm(principal_for(household["partner"]))
        assert await Promotion.objects() == []


async def test_promotion_nonce_is_invisible_without_a_principal(household):
    """An unarmed connection must not see staged shares either."""
    async with transaction_scope():
        await _stage_promotion(household)
    async with transaction_scope():
        assert await Promotion.objects() == []


async def test_forged_promotion_for_another_person_is_rejected(household):
    """WITH CHECK must refuse a nonce staged in someone else's name.

    Without this, one principal could stage a share of their own page
    attributed to the other, and the confirm path's ownership comparison
    would then pass for the wrong person.
    """
    page = None
    async with transaction_scope():
        page = await _seed_private_page(household)
    with pytest.raises(Exception) as exc:
        async with transaction_scope():
            await arm(principal_for(household["partner"]))
            await Promotion(
                person_id=household["wouter"].id,
                source_page_id=page.id,
                source_version=page.version,
                dest_space_id=household["shared"].id,
                dest_path="forged.md",
                section_text="x",
            ).save()
    assert "policy" in str(exc.value).lower()


async def test_no_principal_means_no_rows_even_after_seeding(household):
    """An unarmed transaction returns nothing, never everything."""
    async with transaction_scope():
        await _seed_private_page(household)
    async with transaction_scope():
        assert await Page.objects() == []


async def test_query_outside_a_transaction_returns_nothing(household):
    """Forgetting the transaction scope must fail closed, not leak.

    Piccolo queries are ambient, so there is no session object whose absence
    would be a type error. This is the compensating guarantee: outside a
    transaction the arming ``set_config`` cannot stick to the connection the
    query lands on, so the policies see an empty principal and deny.
    """
    async with transaction_scope():
        await _seed_private_page(household)
    await arm(principal_for(household["wouter"]))  # no transaction -- ineffective
    assert await Page.objects() == []


async def test_principal_does_not_leak_between_concurrent_transactions(household):
    """Interleaved requests must never see each other's principal.

    The pool question, and the one a single-threaded suite would miss: each
    task arms itself, yields mid-transaction so the event loop interleaves
    them, then reads. A connection armed by the other task would show up
    here as the wrong body.
    """
    async with transaction_scope():
        await _seed_private_page(household)
        partner = principal_for(household["partner"])
        await arm(partner)
        await Page(
            space_id=household["p_personal"].id,
            path="hers.md",
            title="Hers",
            tags=[],
            body="hers",
        ).save()

    async def read_as(principal: Principal, expected: list[str]) -> None:
        async with transaction_scope():
            await arm(principal)
            await asyncio.sleep(0.05)  # force interleaving
            bodies = [p.body for p in await Page.objects()]
            assert bodies == expected, f"{principal.email} saw {bodies}"

    wouter, partner = (
        principal_for(household["wouter"]),
        principal_for(household["partner"]),
    )
    await asyncio.gather(
        *[
            read_as(wouter, ["private medical detail"])
            if i % 2 == 0
            else read_as(partner, ["hers"])
            for i in range(20)
        ]
    )


async def test_viewer_row_can_read_but_never_write(household, graph):
    """A hand-inserted VIEWER membership reads the space but cannot write it.

    Nothing creates viewers yet; this pins the RLS split so enabling
    read-only roles later is app-level work, not a policy migration.
    """
    async with transaction_scope():
        page = await _seed_shared_page(household)
    anna = await _admit_viewer(household, graph)

    async with transaction_scope():
        await arm(principal_for(anna))
        visible = await Page.objects().where(Page.id == page.id)
        assert [row.id for row in visible] == [page.id]

    with pytest.raises(Exception) as exc:
        async with transaction_scope():
            await arm(principal_for(anna))
            await Page(
                space_id=household["shared"].id,
                path="planted.md",
                title="x",
                tags=[],
                body="viewer write",
            ).save()
    assert "policy" in str(exc.value).lower()


_WRITABLE_COLUMN = {
    "pages": "body",
    "revisions": "body",
    "attachments": "description",
}


async def test_viewer_can_neither_update_nor_delete_any_content_row(household, graph):
    """A VIEWER reads pages, revisions, and attachments but writes none of them.

    Postgres applies only ``USING`` to ``DELETE``, and ``WITH CHECK`` only to
    the new row of an ``INSERT``/``UPDATE``. A role restriction expressed in
    ``WITH CHECK`` alone therefore leaves deletion governed by the permissive
    read predicate. Piccolo exposes no rowcount, so the denial is measured the
    way Postgres reports it: a ``DELETE``/``UPDATE ... RETURNING id`` returns
    no rows because the policy filtered them out, and the rows are then still
    there when a MEMBER looks. That is the failure mode an exception-based
    test cannot see -- a denied delete removes nothing rather than raising.
    """
    async with transaction_scope():
        page, revision, attachment = await _seed_shared_content(household)
    rows = {"pages": page.id, "revisions": revision.id, "attachments": attachment.id}
    anna = await _admit_viewer(household, graph)

    async with transaction_scope():
        await arm(principal_for(anna))
        for table, row_id in rows.items():
            assert await _row_count(table, row_id) == 1, (
                f"a VIEWER must still read {table}"
            )

        for table in ("revisions", "attachments", "pages"):
            deleted = await Page.raw(
                f"DELETE FROM {table} WHERE id = {{}} RETURNING id", rows[table]
            )
            assert deleted == [], f"a VIEWER deleted {table}"

        for table, row_id in rows.items():
            updated = await Page.raw(
                f"UPDATE {table} SET {_WRITABLE_COLUMN[table]} = 'tampered' "
                f"WHERE id = {{}} RETURNING id",
                row_id,
            )
            assert updated == [], f"a VIEWER updated {table}"

    # Control: every row survived the viewer's attempts, and the same DELETE
    # succeeds for a MEMBER -- so the policies are not merely denying everyone.
    async with transaction_scope():
        await arm(principal_for(household["wouter"]))
        for table, row_id in rows.items():
            assert await _row_count(table, row_id) == 1, f"a VIEWER destroyed {table}"
        member_delete = await Page.raw(
            "DELETE FROM revisions WHERE id = {} RETURNING id", revision.id
        )
        assert len(member_delete) == 1
