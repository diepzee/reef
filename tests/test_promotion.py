from datetime import timedelta
from uuid import UUID

import pytest

from rif.access import Principal
from rif.models import Promotion, utc_now
from rif.pages import get_page, save_page
from rif.promotion import (
    NONCE_TTL,
    PromotionError,
    confirm_promotion,
    prepare_promotion,
)


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_full_flow_copies_stubs_and_discloses(tx, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(
        me, "personal", "boiler.md", "Vaillant ecoTEC", message="x", title="Boiler"
    )
    prepared = await prepare_promotion(me, "boiler.md")
    assert "Vaillant ecoTEC" in prepared["disclosure"]
    result = await confirm_promotion(me, prepared["nonce"])
    assert result["promoted"] is True
    assert (await get_page(theirs, "household", "boiler.md")).body == "Vaillant ecoTEC"
    stub = await get_page(me, "personal", "boiler.md")
    assert "household" in stub.body.lower() and "Vaillant" not in stub.body


async def test_confirm_without_prepare_is_impossible(tx, household):
    from uuid import uuid4

    me = principal_for(household["wouter"])
    with pytest.raises(PromotionError):
        await confirm_promotion(me, str(uuid4()))


async def test_source_changed_since_prepare_fails(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a.md", "v1", message="x")
    prepared = await prepare_promotion(me, "a.md")
    await save_page(me, "personal", "a.md", "v2", message="x")
    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"])


async def test_expired_nonce_is_rejected(tx, household):
    """The 10-minute TTL must reject on its own, independent of DB server locale.

    ``created_at`` is backdated directly rather than by sleeping, so the
    test is fast and exercises the comparison itself rather than the
    passage of wall-clock time.
    """
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "d.md", "stale", message="x")
    prepared = await prepare_promotion(me, "d.md")
    staged = (
        await Promotion.objects().where(Promotion.id == UUID(prepared["nonce"])).first()
    )
    staged.created_at = utc_now() - NONCE_TTL - timedelta(seconds=1)
    await staged.save()
    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"])
    assert await get_page(me, "household", "d.md") is None


async def test_existing_household_page_is_never_overwritten(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "boiler.md", "joint notes", message="x")
    await save_page(me, "personal", "boiler.md", "my notes", message="x")
    prepared = await prepare_promotion(me, "boiler.md")
    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"])
    assert (await get_page(me, "household", "boiler.md")).body == "joint notes"


async def test_confirm_retry_is_idempotent_and_never_copies_the_stub(tx, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(me, "personal", "b.md", "the real content", message="x")
    prepared = await prepare_promotion(me, "b.md")
    await confirm_promotion(me, prepared["nonce"])
    retried = await confirm_promotion(me, prepared["nonce"])
    assert retried["promoted"] is True and retried["already_done"] is True
    assert (await get_page(theirs, "household", "b.md")).body == "the real content"


async def test_another_persons_nonce_is_rejected(tx, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(me, "personal", "c.md", "mine", message="x")
    prepared = await prepare_promotion(me, "c.md")
    with pytest.raises(PromotionError):
        await confirm_promotion(theirs, prepared["nonce"])


SECTION = "## Boiler\n\nVaillant ecoTEC VU 246/5-5, serviced 2025."
REST = "# House\n\n## Roof\n\nTiles redone 2019."
BODY = f"{REST}\n\n{SECTION}"


async def test_section_share_extracts_and_stubs(tx, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(me, "personal", "house-notes.md", BODY, message="x")

    prepared = await prepare_promotion(
        me, "house-notes.md", section=SECTION, dest_path="boiler.md"
    )
    assert prepared["disclosure"] == SECTION
    assert prepared["dest_path"] == "boiler.md"

    result = await confirm_promotion(me, prepared["nonce"])
    assert result["promoted"] is True

    shared = await get_page(theirs, "household", "boiler.md")
    assert shared is not None
    assert shared.body == SECTION

    source = await get_page(me, "personal", "house-notes.md")
    assert "Vaillant" not in source.body
    assert "Tiles redone 2019" in source.body
    assert "boiler.md" in source.body  # marker points at the extracted page


async def test_section_prepare_requires_a_unique_span(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "dup.md", "same\n\nsame", message="x")
    with pytest.raises(PromotionError):
        await prepare_promotion(me, "dup.md", section="same", dest_path="out.md")


async def test_section_share_requires_dest_path(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "a-page.md", BODY, message="x")
    with pytest.raises(PromotionError):
        await prepare_promotion(me, "a-page.md", section=SECTION)


async def test_section_dest_must_not_exist(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "household", "boiler.md", "joint", message="x")
    await save_page(me, "personal", "notes2.md", BODY, message="x")
    prepared = await prepare_promotion(
        me, "notes2.md", section=SECTION, dest_path="boiler.md"
    )
    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"])
    assert (await get_page(me, "household", "boiler.md")).body == "joint"


async def test_section_share_source_changed_since_prepare_fails(tx, household):
    me = principal_for(household["wouter"])
    await save_page(me, "personal", "notes3.md", BODY, message="x")
    prepared = await prepare_promotion(
        me, "notes3.md", section=SECTION, dest_path="out3.md"
    )
    await save_page(me, "personal", "notes3.md", BODY + "\n\nmore", message="x")
    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"])
