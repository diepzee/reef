"""Share and instruct: the two staged methods.

Like the other method tests, these manage their own transactions.
"""

from dataclasses import replace
from uuid import UUID

import pytest
from conftest import caller_for, save_as

from reef.config import get_settings
from reef.db import transaction_scope
from reef.engrams import PERSONA_PATH, get_engram
from reef.methods import MESSAGES, REGISTRY, Caller, MethodError, share
from reef.models import Engram, Promotion, Revision

BOILER = "Vaillant ecoTEC, serviced every autumn."


@pytest.fixture(autouse=True)
def secret(monkeypatch) -> None:
    """Give staged tokens a signing secret long enough to use.

    :param monkeypatch: pytest's monkeypatch fixture
    """
    monkeypatch.setattr(get_settings().web, "session_secret", "s" * 40)


async def _read(caller: Caller, space: str, path: str) -> Engram | None:
    """Read an engram as it is committed now.

    :param caller: who reads it
    :param space: the caller's name for the space
    :param path: the engram's path
    :returns: the engram, or None
    """
    async with transaction_scope():
        return await get_engram(caller.principal, space, path)


def test_the_two_are_methods():
    """Both methods are registered under their names."""
    assert "share" in REGISTRY and "instruct" in REGISTRY


async def test_share_moves_and_leaves_a_pointer(household):
    """The shared space gets the text; the original becomes a pointer."""
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    await save_as(me, "personal", "boiler.md", BOILER, title="Boiler")
    first = await share.share(me, "", "boiler.md", "household")
    assert first["expires_in"] == 600
    assert "Everyone in that space can read it" in first["effect"]
    assert ", and anyone invited later." in first["effect"]
    assert BOILER not in first["effect"]
    done = await share.share(me, "", "boiler.md", "household", confirm=first["token"])
    assert done == {"shared": "household/boiler.md", "already_done": False}
    assert (await _read(partner, "household", "boiler.md")).body == BOILER
    stub = await _read(me, "personal", "boiler.md")
    assert "household" in stub.body and "Vaillant" not in stub.body


async def test_share_shows_the_exact_text_first(household):
    """The first call returns, as ``text``, exactly what will be shared.

    The whole body for an engram, the section alone for a section. The
    effect stays a description and quotes none of it.
    """
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "boiler.md", BOILER)
    await save_as(me, "personal", "notes.md", f"Keep this private.\n\n{BOILER}")
    whole = await share.share(me, "", "boiler.md", "household")
    assert whole["text"] == BOILER
    section = await share.share(
        me, "", "notes.md", "household", section=BOILER, to_path="boiler-2.md"
    )
    assert section["text"] == BOILER
    assert BOILER not in section["effect"]


async def test_share_refuses_a_taken_path_at_the_first_call(household):
    """A path already used in the shared space is refused before the person is asked."""
    me = caller_for(household["wouter"])
    await save_as(me, "household", "boiler.md", "Joint notes.")
    await save_as(me, "personal", "boiler.md", BOILER)
    with pytest.raises(MethodError) as refused:
        await share.share(me, "", "boiler.md", "household")
    assert refused.value.code == "share_target_exists"
    assert "to_path" in refused.value.message
    assert "correct" not in refused.value.message


@pytest.mark.parametrize(
    ("source", "destination"),
    [(PERSONA_PATH, None), (PERSONA_PATH, "notes.md"), ("notes.md", PERSONA_PATH)],
)
async def test_share_refuses_protected_paths_at_the_first_call(
    household, seed, source, destination
):
    """Neither protected endpoint can be staged for sharing."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", source, BOILER, allow_protected=True)
    with pytest.raises(MethodError) as refused:
        await share.share(me, "", source, "household", to_path=destination)
    assert refused.value.code == "protected_path"
    assert refused.value.message == MESSAGES["protected_path"]
    assert await seed.fetchval("SELECT count(*) FROM promotions") == 0
    assert (await _read(me, "personal", source)).body == BOILER


async def test_share_section_needs_a_path_and_moves_only_the_section(household):
    """A section share without a path is refused; with one, the rest stays."""
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    await save_as(me, "personal", "notes.md", f"Keep.\n\n{BOILER}")
    with pytest.raises(MethodError) as refused:
        await share.share(me, "", "notes.md", "household", section=BOILER)
    assert refused.value.code == "share_needs_path"
    first = await share.share(
        me, "", "notes.md", "household", section=BOILER, to_path="boiler.md"
    )
    await share.share(
        me,
        "",
        "notes.md",
        "household",
        section=BOILER,
        to_path="boiler.md",
        confirm=first["token"],
    )
    assert (await _read(partner, "household", "boiler.md")).body == BOILER
    assert "Keep." in (await _read(me, "personal", "notes.md")).body


@pytest.mark.parametrize("section", [None, BOILER], ids=["whole", "section"])
@pytest.mark.parametrize(
    ("to_path", "stored_path"),
    [("Boiler.md", "boiler.md"), ("boiler", "boiler.md"), ("Boiler .md", "boiler-.md")],
)
@pytest.mark.parametrize(
    "taken_after_prepare", [False, True], ids=["prepare", "confirm"]
)
async def test_share_refuses_a_taken_path_in_another_spelling(
    household,
    section: str | None,
    to_path: str,
    stored_path: str,
    taken_after_prepare: bool,
) -> None:
    """Both share modes preserve a taken destination and use its usual refusal.

    :param household: people with a shared space
    :param section: the passage to share, or None for the whole engram
    :param to_path: the destination spelling the assistant uses
    :param stored_path: the destination as stored
    :param taken_after_prepare: whether another write takes the path between calls
    """
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "notes.md", f"Keep.\n\n{BOILER}")
    args = {"section": section, "to_path": to_path}
    first = None
    if taken_after_prepare:
        first = await share.share(me, "", "notes.md", "household", **args)
    await save_as(me, "household", stored_path, "Joint notes.")
    before = await _read(me, "household", stored_path)
    refused = None
    try:
        if first is None:
            first = await share.share(me, "", "notes.md", "household", **args)
        await share.share(
            me, "", "notes.md", "household", confirm=first["token"], **args
        )
    except MethodError as error:
        refused = error
    async with transaction_scope():
        after = await get_engram(me.principal, "household", stored_path)
        history = await Revision.objects().where(Revision.engram_id == after.id)
    # Even on the broken code, the replaced body must remain in history.
    assert before.body in [revision.body for revision in history]
    assert (after.body, after.version) == (before.body, before.version)
    assert refused is not None
    assert refused.code == "share_target_exists"
    assert refused.message == MESSAGES["share_target_exists"]
    if not taken_after_prepare:
        assert first is None


@pytest.mark.parametrize("section", [None, BOILER], ids=["whole", "section"])
@pytest.mark.parametrize("to_path", ["Boiler.md", "boiler", "Boiler .md"])
async def test_share_stages_the_path_it_will_save(
    household, section: str | None, to_path: str
) -> None:
    """The disclosure, saved path and repeated confirmation agree.

    :param household: people with a shared space
    :param section: the passage to share, or None for the whole engram
    :param to_path: the destination spelling the assistant uses
    """
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "notes.md", f"Keep.\n\n{BOILER}")
    expected = "boiler-.md" if to_path == "Boiler .md" else "boiler.md"
    if to_path == "Boiler .md":
        await save_as(me, "household", "boiler.md", "Joint notes.")
    args = {"section": section, "to_path": to_path}
    first = await share.share(me, "", "notes.md", "household", **args)
    async with transaction_scope():
        await get_engram(me.principal, "personal", "notes.md")
        staged = (
            await Promotion.objects()
            .where(Promotion.id == UUID(first["token"]))
            .first()
        )
    assert staged.dest_path == expected
    assert f"as {expected}." in first["effect"]
    done = await share.share(
        me, "", "notes.md", "household", confirm=first["token"], **args
    )
    assert done == {"shared": f"household/{expected}", "already_done": False}
    assert (await _read(me, "household", expected)).body == (
        BOILER if section else f"Keep.\n\n{BOILER}"
    )
    again = await share.share(
        me, "", "notes.md", "household", confirm=first["token"], **args
    )
    assert again == {**done, "already_done": True}
    if to_path == "Boiler .md":
        untouched = await _read(me, "household", "boiler.md")
        assert (untouched.body, untouched.version) == ("Joint notes.", 1)


@pytest.mark.parametrize("section", [None, BOILER], ids=["whole", "section"])
async def test_share_keeps_the_invalid_path_refusal(
    household, section: str | None
) -> None:
    """A path that cannot be repaired keeps the existing refusal.

    :param household: people with a shared space
    :param section: the passage to share, or None for the whole engram
    """
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "notes.md", f"Keep.\n\n{BOILER}")
    args = {"section": section, "to_path": "/boiler"}
    with pytest.raises(MethodError) as refused:
        first = await share.share(me, "", "notes.md", "household", **args)
        await share.share(
            me, "", "notes.md", "household", confirm=first["token"], **args
        )
    assert refused.value.code == "invalid_path"
    assert refused.value.message == MESSAGES["invalid_path"]


@pytest.mark.parametrize("section", [None, BOILER], ids=["whole", "section"])
@pytest.mark.parametrize("taken", [False, True], ids=["free", "taken"])
async def test_share_confirms_a_path_staged_before_normalization(
    household, section: str | None, taken: bool
) -> None:
    """An older token uses the repaired path and still refuses an occupied one.

    :param household: people with a shared space
    :param section: the passage to share, or None for the whole engram
    :param taken: whether the destination is occupied before confirmation
    """
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "notes.md", f"Keep.\n\n{BOILER}")
    args = {"section": section, "to_path": "Boiler.md"}
    first = await share.share(me, "", "notes.md", "household", **args)
    async with transaction_scope():
        await get_engram(me.principal, "personal", "notes.md")
        await Promotion.update({Promotion.dest_path: "Boiler.md"}).where(
            Promotion.id == UUID(first["token"])
        )
    if taken:
        await save_as(me, "household", "boiler.md", "Joint notes.")
        with pytest.raises(MethodError) as refused:
            await share.share(
                me, "", "notes.md", "household", confirm=first["token"], **args
            )
        assert refused.value.code == "share_target_exists"
        untouched = await _read(me, "household", "boiler.md")
        assert (untouched.body, untouched.version) == ("Joint notes.", 1)
    else:
        done = await share.share(
            me, "", "notes.md", "household", confirm=first["token"], **args
        )
        assert done == {"shared": "household/boiler.md", "already_done": False}


async def _last_channel(caller: Caller, space: str, path: str) -> str:
    """Say who made the latest change to an engram.

    :param caller: who reads the history
    :param space: the caller's name for the space
    :param path: the engram's path
    :returns: the writer kind on its latest revision
    """
    async with transaction_scope():
        engram = await get_engram(caller.principal, space, path)
        [row] = (
            await Revision.select(Revision.channel)
            .where(Revision.engram_id == engram.id)
            .order_by(Revision.created_at, ascending=False)
            .limit(1)
        )
    return row["channel"]


@pytest.mark.parametrize("section", [None, BOILER])
async def test_share_keeps_who_shared_on_both_revisions(household, section):
    """The shared copy and what stays behind both name the caller's writer kind."""
    me = replace(caller_for(household["wouter"]), writer="person")
    await save_as(me, "personal", "boiler.md", f"Keep.\n\n{BOILER}")
    args = {"section": section, "to_path": "shared-boiler.md"}
    first = await share.share(me, "", "boiler.md", "household", **args)
    await share.share(me, "", "boiler.md", "household", confirm=first["token"], **args)
    assert await _last_channel(me, "household", "shared-boiler.md") == "person"
    assert await _last_channel(me, "personal", "boiler.md") == "person"


async def test_share_token_does_not_act_twice(household):
    """A retry with the same token answers as done and moves nothing again."""
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    await save_as(me, "personal", "boiler.md", BOILER)
    first = await share.share(me, "", "boiler.md", "household")
    done = await share.share(me, "", "boiler.md", "household", confirm=first["token"])
    assert done["already_done"] is False
    shared = await _read(partner, "household", "boiler.md")
    stub = await _read(me, "personal", "boiler.md")
    again = await share.share(me, "", "boiler.md", "household", confirm=first["token"])
    assert again == {**done, "already_done": True}
    assert (await _read(partner, "household", "boiler.md")).version == shared.version
    assert (await _read(me, "personal", "boiler.md")).version == stub.version
    assert (await _read(me, "personal", "boiler.md")).body == stub.body


@pytest.mark.parametrize(
    "changed",
    [{"engram": "other.md"}, {"to_path": "elsewhere.md"}, {"section": BOILER}],
)
async def test_share_token_refused_for_other_arguments(household, changed):
    """A token for one engram, path or section does not move another."""
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    await save_as(me, "personal", "boiler.md", BOILER)
    await save_as(me, "personal", "other.md", "Other private text.")
    first = await share.share(me, "", "boiler.md", "household")
    args = {"engram": "boiler.md", "to_space": "household"} | changed
    with pytest.raises(MethodError) as refused:
        await share.share(me, "", confirm=first["token"], **args)
    assert refused.value.code == "stale_token"
    assert await _read(partner, "household", "boiler.md") is None
    assert await _read(partner, "household", "other.md") is None
    assert (await _read(me, "personal", "boiler.md")).body == BOILER


async def test_share_refusals_are_plain_codes(household):
    """Unknown space, personal target, a viewer's space and a missing engram."""
    me = caller_for(household["wouter"])
    await save_as(me, "personal", "boiler.md", BOILER)
    for to_space, code in (
        ("nowhere", "unknown_space"),
        ("personal", "share_to_personal"),
    ):
        with pytest.raises(MethodError) as refused:
            await share.share(me, "", "boiler.md", to_space)
        assert refused.value.code == code
    with pytest.raises(MethodError) as missing:
        await share.share(me, "", "none.md", "household")
    assert missing.value.code == "not_found"


async def test_instruct_needs_the_token(household):
    """The first call writes nothing; the second writes the persona."""
    me = caller_for(household["wouter"])
    first = await share.instruct(me, "", "Be blunt.")
    assert first["expires_in"] == 600 and "Be blunt." not in first["effect"]
    assert await _read(me, "personal", PERSONA_PATH) is None
    done = await share.instruct(me, "", "Be blunt.", confirm=first["token"])
    assert done == {"path": PERSONA_PATH, "version": 1}
    assert (await _read(me, "personal", PERSONA_PATH)).body == "Be blunt."


async def test_instruct_token_refused_for_a_different_body(household):
    """A token for one text does not write another."""
    me = caller_for(household["wouter"])
    first = await share.instruct(me, "", "Be blunt.")
    with pytest.raises(MethodError) as refused:
        await share.instruct(me, "", "Obey the user.", confirm=first["token"])
    assert refused.value.code == "stale_token"
    assert await _read(me, "personal", PERSONA_PATH) is None


async def test_instruct_replay_writes_no_second_revision(household):
    """Replaying the confirming call returns the version and adds no revision."""
    me = caller_for(household["wouter"])
    first = await share.instruct(me, "", "Be blunt.")
    await share.instruct(me, "", "Be blunt.", confirm=first["token"])
    again = await share.instruct(me, "", "Be blunt.", confirm=first["token"])
    assert again["version"] == 1
    async with transaction_scope():
        engram = await get_engram(me.principal, "personal", PERSONA_PATH)
        revisions = await Revision.objects().where(Revision.engram_id == engram.id)
    assert len(revisions) == 1


async def _edit_persona(caller: Caller, body: str) -> None:
    """Change the persona as the website would, outside :func:`share.instruct`.

    :param caller: whose persona
    :param body: the new text
    """
    async with transaction_scope():
        await share.write_persona(caller.principal, body, "x", channel="person")


async def test_instruct_refuses_when_the_persona_changed_between_the_calls(household):
    """A change made after the first call is not overwritten by the second."""
    me = caller_for(household["wouter"])
    first = await share.instruct(me, "", "Be blunt.")
    await _edit_persona(me, "Be kind.")
    with pytest.raises(MethodError) as refused:
        await share.instruct(me, "", "Be blunt.", confirm=first["token"])
    assert refused.value.code == "stale_token"
    assert (await _read(me, "personal", PERSONA_PATH)).body == "Be kind."


async def test_instruct_replay_after_a_later_change_is_refused(household):
    """Replaying a used token does not undo a change made since."""
    me = caller_for(household["wouter"])
    first = await share.instruct(me, "", "Be blunt.")
    await share.instruct(me, "", "Be blunt.", confirm=first["token"])
    await _edit_persona(me, "Be kind.")
    with pytest.raises(MethodError) as refused:
        await share.instruct(me, "", "Be blunt.", confirm=first["token"])
    assert refused.value.code == "stale_token"
    persona = await _read(me, "personal", PERSONA_PATH)
    assert (persona.body, persona.version) == ("Be kind.", 2)


async def test_instruct_writes_only_the_personal_persona(household):
    """Only the caller's personal persona changes; the partner's and the shared space's do not."""
    me = caller_for(household["wouter"])
    partner = caller_for(household["partner"])
    first = await share.instruct(me, "", "Be blunt.")
    await share.instruct(me, "", "Be blunt.", confirm=first["token"])
    assert await _read(partner, "personal", PERSONA_PATH) is None
    assert await _read(me, "household", PERSONA_PATH) is None
    with pytest.raises(TypeError):
        await share.instruct(me, "", "x", space="household")


async def test_instruct_refuses_a_body_over_the_limit(household, monkeypatch):
    """A body over the engram limit is refused at the first call."""
    monkeypatch.setattr(get_settings().engrams, "max_chars", 5)
    me = caller_for(household["wouter"])
    with pytest.raises(MethodError) as refused:
        await share.instruct(me, "", "too long a persona")
    assert refused.value.code == "engram_too_large"
