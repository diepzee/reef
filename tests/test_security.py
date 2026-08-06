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
from rif.models import Page, Revision


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
