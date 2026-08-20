"""The share ceremony as a boundary rather than a convention.

The attack these are written against: a co-member plants instruction-shaped
text where it lands in the victim's ``load_index`` output, and the assistant
-- holding both ``read_page(personal, …)`` and ``write_page(<cove>, …)`` --
copies a private page into the cove. Being persuaded used to be sufficient.
"""

import pytest

from reef.access import Principal, resolve_cove
from reef.db import transaction_scope
from reef.leakguard import SHINGLE_WORDS, overlaps, shingles
from reef.pages import PrivateContentLeak, edit_section, get_page, save_page
from reef.promotion import confirm_promotion, prepare_promotion
from reef.server import tool_remember

DIARY = (
    "I was diagnosed HIV positive in the spring of 2019 and have been on "
    "antiretroviral treatment ever since, which my employer does not know "
    "about and which I have told almost nobody in my family."
)


def principal_for(person) -> Principal:
    """Build a principal from a seeded person row.

    :param person: a Person row
    :returns: the matching principal
    """
    return Principal(person_id=person.id, email=person.email)


async def _seed_private(principal: Principal, body: str = DIARY) -> None:
    """Write a private page into the principal's personal cove.

    :param principal: the authenticated person
    :param body: the private text
    """
    async with transaction_scope():
        await save_page(principal, "personal", "health.md", body, message="private")


def test_shingles_ignore_formatting_but_not_wording():
    """Reformatting is what an assistant does routinely; it is not evasion."""
    words = " ".join(f"word{n}" for n in range(SHINGLE_WORDS))
    assert shingles(words) == shingles(f"  {words.upper()}!\n\n")
    assert not shingles(words) & shingles(words.replace("word3", "other"))


def test_text_shorter_than_a_shingle_cannot_match():
    """A stated limit, not an oversight: short facts are not protected."""
    short = " ".join(f"word{n}" for n in range(SHINGLE_WORDS - 1))
    assert shingles(short) == set()
    assert overlaps(short, "", [short]) is False


async def test_a_personal_page_cannot_be_written_straight_into_a_cove(household):
    """The exfiltration itself: one call, no nonce, no disclosure."""
    wouter = principal_for(household["wouter"])
    await _seed_private(wouter)

    async with transaction_scope():
        private = await get_page(wouter, "personal", "health.md")
        with pytest.raises(PrivateContentLeak, match="prepare_to_share"):
            await save_page(
                wouter, "household", "notes.md", private.body, message="notes"
            )

    async with transaction_scope():
        assert await get_page(wouter, "household", "notes.md") is None


async def test_the_co_member_never_sees_it(household):
    """End to end, from the other side of the cove."""
    wouter = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await _seed_private(wouter)

    async with transaction_scope():
        private = await get_page(wouter, "personal", "health.md")
        with pytest.raises(PrivateContentLeak):
            await save_page(
                wouter,
                "household",
                "leak.md",
                f"# Notes\n\n{private.body}\n",
                message="notes",
            )

    async with transaction_scope():
        assert await get_page(partner, "household", "leak.md") is None


async def test_edit_section_is_guarded_too(household):
    """Otherwise the guard is one tool call wide."""
    wouter = principal_for(household["wouter"])
    await _seed_private(wouter)
    async with transaction_scope():
        await save_page(
            wouter, "household", "notes.md", "# Notes\n\nplaceholder\n", message="seed"
        )

    async with transaction_scope():
        with pytest.raises(PrivateContentLeak):
            await edit_section(
                wouter, "household", "notes.md", "placeholder", DIARY, message="edit"
            )


async def test_remember_cannot_smuggle_a_private_page_into_a_cove(household):
    """It appends through save_page, so it inherits the guard."""
    wouter = principal_for(household["wouter"])
    await _seed_private(wouter)

    async with transaction_scope():
        with pytest.raises(PrivateContentLeak):
            await tool_remember(wouter, DIARY, cove="household")


async def test_the_share_ceremony_still_works(household):
    """The guard exists to force writes through here, so here it must not fire."""
    wouter = principal_for(household["wouter"])
    await _seed_private(wouter)

    async with transaction_scope():
        staged = await prepare_promotion(wouter, "health.md", "household")
    async with transaction_scope():
        outcome = await confirm_promotion(wouter, staged["nonce"])

    assert outcome["promoted"] is True
    async with transaction_scope():
        shared = await get_page(
            principal_for(household["partner"]), "household", "health.md"
        )
    assert "HIV positive" in shared.body


async def test_a_section_share_still_works(household):
    """The section path writes the extracted span; it is equally sanctioned."""
    wouter = principal_for(household["wouter"])
    await _seed_private(wouter, f"# Health\n\n{DIARY}\n\nUnrelated line.\n")

    async with transaction_scope():
        staged = await prepare_promotion(
            wouter, "health.md", "household", section=DIARY, dest_path="status.md"
        )
    async with transaction_scope():
        assert (await confirm_promotion(wouter, staged["nonce"]))["promoted"] is True


async def test_ordinary_cove_writing_is_unaffected(household):
    """The guard must be invisible to somebody simply using the product."""
    wouter = principal_for(household["wouter"])
    await _seed_private(wouter)

    async with transaction_scope():
        page = await save_page(
            wouter,
            "household",
            "shopping.md",
            "# Shopping\n\nMilk, bread, and a new filter for the coffee machine.\n",
            message="list",
        )
    assert page.version == 1


async def test_re_saving_an_already_shared_page_does_not_start_failing(household):
    """The false positive that would make the guard unusable.

    Content legitimately shared, then re-added to the personal cove, must
    not make every later edit of the shared page fail -- so only the text a
    write *introduces* is judged.
    """
    wouter = principal_for(household["wouter"])
    async with transaction_scope():
        await save_page(wouter, "household", "trip.md", DIARY, message="shared first")
    # The same words now also exist privately.
    await _seed_private(wouter)

    async with transaction_scope():
        page = await save_page(
            wouter,
            "household",
            "trip.md",
            f"{DIARY}\n\nAdded a line.\n",
            message="edit",
        )
    assert page.version == 2


async def test_a_co_member_can_still_write_their_own_words(household):
    """The guard is per-caller: it reads the writer's personal cove, never
    anybody else's, so it cannot leak whether a stranger wrote something."""
    wouter = principal_for(household["wouter"])
    partner = principal_for(household["partner"])
    await _seed_private(wouter)

    async with transaction_scope():
        page = await save_page(
            partner, "household", "theirs.md", DIARY, message="partner's own words"
        )
    assert page.version == 1


async def test_the_persona_is_exempt(household):
    """Seeded from a fixed template, so its wording is shared by everyone and
    matching against it would refuse writes over nothing private."""
    wouter = principal_for(household["wouter"])
    async with transaction_scope():
        persona = await get_page(wouter, "personal", "meta/persona.md")
    assert persona is None or persona.body

    async with transaction_scope():
        await resolve_cove(wouter, "personal")
        stub = (
            "Not yet written. This is a first meeting: introduce yourself, ask "
            "what the user would like to call you, and interview gently to fill "
            "this page in."
        )
        page = await save_page(wouter, "household", "about.md", stub, message="ok")
    assert page.version == 1
