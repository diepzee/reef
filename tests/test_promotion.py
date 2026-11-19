from datetime import timedelta
from uuid import UUID

import pytest

from reef.access import Principal
from reef.engrams import get_engram, save_engram
from reef.models import Promotion, utc_now
from reef.promotion import (
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
    await save_engram(
        me, "personal", "boiler.md", "Vaillant ecoTEC", message="x", title="Boiler"
    )
    prepared = await prepare_promotion(me, "boiler.md", "household")
    assert "Vaillant ecoTEC" in prepared["disclosure"]
    result = await confirm_promotion(me, prepared["nonce"], channel="assistant")
    assert result["promoted"] is True
    assert (
        await get_engram(theirs, "household", "boiler.md")
    ).body == "Vaillant ecoTEC"
    stub = await get_engram(me, "personal", "boiler.md")
    assert "household" in stub.body.lower() and "Vaillant" not in stub.body


async def test_whole_engram_share_with_a_new_name_stubs_the_source_only(tx, household):
    """A whole-engram share stubs the engram that moved, never the engram it was renamed to.

    The stub belongs to the source: it is what stays behind in the personal
    space. Writing it to ``dest_path`` instead left the source's full body
    private-but-unmarked and overwrote whatever unrelated personal engram
    happened to carry that name — data loss the user never saw disclosed.
    """
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_engram(me, "personal", "a.md", "the a body", message="x")
    await save_engram(me, "personal", "b.md", "unrelated b body", message="x")

    prepared = await prepare_promotion(me, "a.md", "household", dest_path="b.md")
    await confirm_promotion(me, prepared["nonce"], channel="assistant")

    assert (await get_engram(theirs, "household", "b.md")).body == "the a body"
    stub = await get_engram(me, "personal", "a.md")
    assert "the a body" not in stub.body and "household" in stub.body
    assert (await get_engram(me, "personal", "b.md")).body == "unrelated b body"


async def test_confirm_without_prepare_is_impossible(tx, household):
    from uuid import uuid4

    me = principal_for(household["wouter"])
    with pytest.raises(PromotionError):
        await confirm_promotion(me, str(uuid4()), channel="assistant")


async def test_source_changed_since_prepare_fails(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a.md", "v1", message="x")
    prepared = await prepare_promotion(me, "a.md", "household")
    await save_engram(me, "personal", "a.md", "v2", message="x")
    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"], channel="assistant")


async def test_expired_nonce_is_rejected(tx, household):
    """The 10-minute TTL must reject on its own, independent of DB server locale.

    ``created_at`` is backdated directly rather than by sleeping, so the
    test is fast and exercises the comparison itself rather than the
    passage of wall-clock time.
    """
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "d.md", "stale", message="x")
    prepared = await prepare_promotion(me, "d.md", "household")
    staged = (
        await Promotion.objects().where(Promotion.id == UUID(prepared["nonce"])).first()
    )
    staged.created_at = utc_now() - NONCE_TTL - timedelta(seconds=1)
    await staged.save()
    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"], channel="assistant")
    assert await get_engram(me, "household", "d.md") is None


async def test_existing_household_engram_is_never_overwritten(tx, household):
    """A path taken before the share is refused at prepare time."""
    me = principal_for(household["wouter"])
    await save_engram(me, "household", "boiler.md", "joint notes", message="x")
    await save_engram(me, "personal", "boiler.md", "my notes", message="x")
    with pytest.raises(PromotionError) as refused:
        await prepare_promotion(me, "boiler.md", "household")
    assert refused.value.code == "share_target_exists"
    assert (await get_engram(me, "household", "boiler.md")).body == "joint notes"


async def test_a_path_taken_after_prepare_is_refused_at_confirm(tx, household):
    """A path taken between the two steps is refused at confirm time."""
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "boiler.md", "my notes", message="x")
    prepared = await prepare_promotion(me, "boiler.md", "household")
    await save_engram(me, "household", "boiler.md", "joint notes", message="x")
    with pytest.raises(PromotionError) as refused:
        await confirm_promotion(me, prepared["nonce"], channel="assistant")
    assert refused.value.code == "share_target_exists"
    assert (await get_engram(me, "household", "boiler.md")).body == "joint notes"


async def test_confirm_retry_is_idempotent_and_never_copies_the_stub(tx, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_engram(me, "personal", "b.md", "the real content", message="x")
    prepared = await prepare_promotion(me, "b.md", "household")
    await confirm_promotion(me, prepared["nonce"], channel="assistant")
    retried = await confirm_promotion(me, prepared["nonce"], channel="assistant")
    assert retried["promoted"] is True and retried["already_done"] is True
    assert (await get_engram(theirs, "household", "b.md")).body == "the real content"


async def test_another_persons_nonce_is_rejected(tx, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_engram(me, "personal", "c.md", "mine", message="x")
    prepared = await prepare_promotion(me, "c.md", "household")
    with pytest.raises(PromotionError):
        await confirm_promotion(theirs, prepared["nonce"], channel="assistant")


SECTION = "## Boiler\n\nVaillant ecoTEC VU 246/5-5, serviced 2025."
REST = "# House\n\n## Roof\n\nTiles redone 2019."
BODY = f"{REST}\n\n{SECTION}"


async def test_section_share_extracts_and_stubs(tx, household):
    me = principal_for(household["wouter"])
    theirs = principal_for(household["partner"])
    await save_engram(me, "personal", "house-notes.md", BODY, message="x")

    prepared = await prepare_promotion(
        me, "house-notes.md", "household", section=SECTION, dest_path="boiler.md"
    )
    assert prepared["disclosure"] == SECTION
    assert prepared["dest_path"] == "boiler.md"

    result = await confirm_promotion(me, prepared["nonce"], channel="assistant")
    assert result["promoted"] is True

    shared = await get_engram(theirs, "household", "boiler.md")
    assert shared is not None
    assert shared.body == SECTION

    source = await get_engram(me, "personal", "house-notes.md")
    assert "Vaillant" not in source.body
    assert "Tiles redone 2019" in source.body
    assert "boiler.md" in source.body  # marker points at the extracted engram


async def test_section_prepare_requires_a_unique_span(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "dup.md", "same\n\nsame", message="x")
    with pytest.raises(PromotionError):
        await prepare_promotion(
            me, "dup.md", "household", section="same", dest_path="out.md"
        )


async def test_section_share_requires_dest_path(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "a-engram.md", BODY, message="x")
    with pytest.raises(PromotionError):
        await prepare_promotion(me, "a-engram.md", "household", section=SECTION)


async def test_section_dest_must_not_exist(tx, household):
    """A section share to a taken path is refused before it is staged."""
    me = principal_for(household["wouter"])
    await save_engram(me, "household", "boiler.md", "joint", message="x")
    await save_engram(me, "personal", "notes2.md", BODY, message="x")
    with pytest.raises(PromotionError) as refused:
        await prepare_promotion(
            me, "notes2.md", "household", section=SECTION, dest_path="boiler.md"
        )
    assert refused.value.code == "share_target_exists"
    assert (await get_engram(me, "household", "boiler.md")).body == "joint"


async def test_section_share_source_changed_since_prepare_fails(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "notes3.md", BODY, message="x")
    prepared = await prepare_promotion(
        me, "notes3.md", "household", section=SECTION, dest_path="out3.md"
    )
    await save_engram(me, "personal", "notes3.md", BODY + "\n\nmore", message="x")
    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"], channel="assistant")


async def test_share_targets_a_chosen_space(tx, household, graph):
    me = principal_for(household["wouter"])
    await graph.shared_space("trip", household["wouter"])
    await save_engram(me, "personal", "packing.md", "tent, stove", message="x")
    prepared = await prepare_promotion(me, "packing.md", "trip")
    assert prepared["dest_cove"] == "trip"
    await confirm_promotion(me, prepared["nonce"], channel="assistant")
    assert (await get_engram(me, "trip", "packing.md")).body == "tent, stove"
    assert await get_engram(me, "household", "packing.md") is None


async def test_disclosure_enumerates_the_destination_members(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "m.md", "x", message="x")
    prepared = await prepare_promotion(me, "m.md", "household")
    assert sorted(prepared["members"]) == ["Partner", "Wouter"]
    assert "Partner" in prepared["warning"]


async def test_share_to_unjoined_or_personal_space_is_refused(tx, household, graph):
    me = principal_for(household["wouter"])
    stranger = await graph.person("carla@example.test", "Carla")
    await graph.shared_space("carla-club", stranger)
    await save_engram(me, "personal", "n.md", "x", message="x")
    with pytest.raises(PromotionError):
        await prepare_promotion(me, "n.md", "carla-club")
    with pytest.raises(PromotionError):
        await prepare_promotion(me, "n.md", "personal")


async def test_confirm_rechecks_membership_lost_after_prepare(tx, graph):
    owner2 = await graph.person("owner2@example.test", "Owner2")
    wouter = await graph.person("wouter2@example.test", "Wouter2")
    await graph.personal_space(wouter, slug="wouter2")
    club = await graph.shared_space("club", owner2, wouter)
    me = principal_for(wouter)
    owner_principal = principal_for(owner2)

    await save_engram(me, "personal", "invite.md", "bring snacks", message="x")
    prepared = await prepare_promotion(me, "invite.md", "club")

    # No owner-removes-member DELETE policy exists; removal lives in
    # reef_remove_member. Seeding the loss directly keeps this test about
    # what confirm() rechecks rather than about how the row went away.
    await graph.drop_membership(wouter, club)

    with pytest.raises(PromotionError):
        await confirm_promotion(me, prepared["nonce"], channel="assistant")
    assert await get_engram(owner_principal, "club", "invite.md") is None
