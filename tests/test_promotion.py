import pytest

from rif.access import Principal
from rif.pages import get_page, save_page
from rif.promotion import PromotionError, confirm_promotion, prepare_promotion


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_full_flow_copies_stubs_and_discloses(session, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(session, me, "personal", "boiler.md", "Vaillant ecoTEC",
                    message="x", title="Boiler")
    prepared = await prepare_promotion(session, me, "boiler.md")
    assert "Vaillant ecoTEC" in prepared["disclosure"]
    result = await confirm_promotion(session, me, prepared["nonce"])
    assert result["promoted"] is True
    assert (await get_page(session, theirs, "household", "boiler.md")).body == "Vaillant ecoTEC"
    stub = await get_page(session, me, "personal", "boiler.md")
    assert "household" in stub.body.lower() and "Vaillant" not in stub.body


async def test_confirm_without_prepare_is_impossible(session, household):
    from uuid import uuid4

    me = principal_for(household["wouter"])
    with pytest.raises(PromotionError):
        await confirm_promotion(session, me, str(uuid4()))


async def test_source_changed_since_prepare_fails(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "personal", "a.md", "v1", message="x")
    prepared = await prepare_promotion(session, me, "a.md")
    await save_page(session, me, "personal", "a.md", "v2", message="x")
    with pytest.raises(PromotionError):
        await confirm_promotion(session, me, prepared["nonce"])


async def test_existing_household_page_is_never_overwritten(session, household):
    me = principal_for(household["wouter"])
    await save_page(session, me, "household", "boiler.md", "joint notes", message="x")
    await save_page(session, me, "personal", "boiler.md", "my notes", message="x")
    prepared = await prepare_promotion(session, me, "boiler.md")
    with pytest.raises(PromotionError):
        await confirm_promotion(session, me, prepared["nonce"])
    assert (await get_page(session, me, "household", "boiler.md")).body == "joint notes"


async def test_confirm_retry_is_idempotent_and_never_copies_the_stub(session, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(session, me, "personal", "b.md", "the real content", message="x")
    prepared = await prepare_promotion(session, me, "b.md")
    await confirm_promotion(session, me, prepared["nonce"])
    retried = await confirm_promotion(session, me, prepared["nonce"])
    assert retried["promoted"] is True and retried["already_done"] is True
    assert (await get_page(session, theirs, "household", "b.md")).body == "the real content"


async def test_another_persons_nonce_is_rejected(session, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_page(session, me, "personal", "c.md", "mine", message="x")
    prepared = await prepare_promotion(session, me, "c.md")
    with pytest.raises(PromotionError):
        await confirm_promotion(session, theirs, prepared["nonce"])
