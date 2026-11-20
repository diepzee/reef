"""Full-text search over engrams, inside the same RLS session as every read.

The competitive positioning this feature carries: a search that forgets a
filter returns nothing, not somebody else's memories. The security tests
here are the point, not an afterthought.
"""

import pytest

from reef.access import AccessDenied, Principal, arm
from reef.config import get_settings
from reef.engrams import save_engram
from reef.models import Attachment, AttachmentStatus, Engram
from reef.search import search_engrams


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def _add_file(principal, space, key, filename, description, status=None):
    await arm(principal)
    await Attachment(
        space_id=space.id,
        object_key=key,
        filename=filename,
        mime="application/pdf",
        byte_size=1,
        description=description,
        status=(status or AttachmentStatus.READY).value,
    ).save()


async def test_finds_an_engram_by_words_in_its_body(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "house.md",
        "The boiler is a Vaillant ecoTEC and was serviced in March.",
        message="x",
        title="House",
    )
    results = await search_engrams(me, "vaillant boiler")
    assert [r["path"] for r in results] == ["house.md"]
    assert results[0]["cove"] == "personal"
    assert results[0]["title"] == "House"


async def test_snippet_bolds_the_matched_words(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "house.md",
        "A long preamble about the garden. The boiler is a Vaillant. "
        "More text about gutters follows here.",
        message="x",
    )
    results = await search_engrams(me, "boiler")
    assert "**boiler**" in results[0]["snippet"]


async def test_searches_every_accessible_space_and_labels_by_alias(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "notes.md", "The boiler manual.", message="x")
    await save_engram(me, "household", "house.md", "The boiler is new.", message="x")
    results = await search_engrams(me, "boiler")
    assert {(r["cove"], r["path"]) for r in results} == {
        ("personal", "notes.md"),
        ("household", "house.md"),
    }


async def test_title_match_outranks_body_match(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "mentions.md",
        "The insurance paperwork mentions the boiler once.",
        message="x",
    )
    await save_engram(
        me,
        "personal",
        "boiler.md",
        "Service history and serial numbers.",
        message="x",
        title="Boiler",
    )
    results = await search_engrams(me, "boiler")
    assert [r["path"] for r in results] == ["boiler.md", "mentions.md"]


async def test_scoping_to_one_space_excludes_the_others(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "notes.md", "The boiler manual.", message="x")
    await save_engram(me, "household", "house.md", "The boiler is new.", message="x")
    results = await search_engrams(me, "boiler", space="household")
    assert [(r["cove"], r["path"]) for r in results] == [("household", "house.md")]


async def test_no_match_returns_an_empty_list(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "notes.md", "Nothing relevant.", message="x")
    assert await search_engrams(me, "submarine") == []


async def test_blank_query_returns_an_empty_list(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "notes.md", "The boiler manual.", message="x")
    assert await search_engrams(me, "   ") == []


async def test_unknown_space_is_denied_like_any_read(tx, household):
    me = principal_for(household["wouter"])
    with pytest.raises(AccessDenied):
        await search_engrams(me, "boiler", space="not-mine")


async def test_search_cannot_see_another_persons_personal_engrams(tx, household):
    """The raw SQL walks the whole engrams table; RLS must be what scopes it.

    The positive control (the owner finds their own engram) proves the test
    would catch a policy that hid everything, not just one that leaked.
    """
    partner = principal_for(household["partner"])
    await save_engram(
        partner, "personal", "diary.md", "The xylophone lessons resume.", message="x"
    )
    assert await search_engrams(partner, "xylophone") != []
    me = principal_for(household["wouter"])
    assert await search_engrams(me, "xylophone") == []


async def test_shared_space_matches_reach_both_members(tx, household):
    partner = principal_for(household["partner"])
    await save_engram(
        partner, "household", "house.md", "The xylophone lives here.", message="x"
    )
    me = principal_for(household["wouter"])
    results = await search_engrams(me, "xylophone")
    assert [(r["cove"], r["path"]) for r in results] == [("household", "house.md")]


async def test_finds_a_file_by_its_description(tx, household):
    me = principal_for(household["wouter"])
    await _add_file(
        me,
        household["w_personal"],
        "attachments/lease",
        "lease.pdf",
        "Signed rental agreement for the flat.",
    )
    results = await search_engrams(me, "rental agreement")
    assert len(results) == 1
    hit = results[0]
    assert hit["kind"] == "file"
    assert hit["cove"] == "personal"
    assert hit["key"] == "attachments/lease"
    assert hit["filename"] == "lease.pdf"
    assert "**rental**" in hit["snippet"]


async def test_engram_results_say_they_are_engrams(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "notes.md", "The boiler manual.", message="x")
    results = await search_engrams(me, "boiler")
    assert results[0]["kind"] == "page"


async def test_a_file_whose_bytes_never_landed_is_not_searchable(tx, household):
    me = principal_for(household["wouter"])
    await _add_file(
        me,
        household["w_personal"],
        "attachments/ghost",
        "ghost.pdf",
        "A xylophone maintenance guide.",
        status=AttachmentStatus.PENDING,
    )
    assert await search_engrams(me, "xylophone") == []


async def test_search_cannot_see_another_persons_files(tx, household):
    partner = principal_for(household["partner"])
    await _add_file(
        partner,
        household["p_personal"],
        "attachments/private",
        "private.pdf",
        "A xylophone recital programme.",
    )
    assert await search_engrams(partner, "xylophone") != []
    me = principal_for(household["wouter"])
    assert await search_engrams(me, "xylophone") == []


async def test_query_syntax_garbage_cannot_error(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "notes.md", "The boiler manual.", message="x")
    for garbage in ["boiler AND (", '"unclosed phrase', "a OR OR b", "-"]:
        await search_engrams(me, garbage)


# --- stored vectors, in two languages ----------------------------------------


async def test_dutch_word_forms_reach_their_stem(tx, household):
    """``contracten`` is found by ``contract`` only through the Dutch stemmer."""
    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "werk.md",
        "De contracten worden in maart verlengd.",
        message="x",
        title="Werk",
    )
    results = await search_engrams(me, "contract")
    assert [r["path"] for r in results] == ["werk.md"]


async def test_a_dutch_only_match_is_still_highlighted(tx, household):
    """The snippet is cut under the configuration that actually matched."""
    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "werk.md",
        "Een lange inleiding over de tuin. De contracten worden verlengd. "
        "Daarna volgt nog tekst over de dakgoten.",
        message="x",
    )
    results = await search_engrams(me, "contract")
    assert "**contracten**" in results[0]["snippet"]


async def test_english_word_forms_still_reach_their_stem(tx, household):
    """Indexing Dutch beside English takes nothing away from English."""
    me = principal_for(household["wouter"])
    await save_engram(
        me, "personal", "house.md", "The boilers were serviced.", message="x"
    )
    results = await search_engrams(me, "boiler servicing")
    assert [r["path"] for r in results] == ["house.md"]
    assert "**boilers**" in results[0]["snippet"]


async def test_a_file_with_an_enormous_description_is_stored_and_found(tx, household):
    """An unbounded description cannot push its search vector past the limit.

    Postgres refuses a ``tsvector`` over one megabyte. Descriptions have no
    ceiling, so without a bound on what is indexed this insert would fail --
    and before vectors were stored, it was every later search that failed.
    """
    me = principal_for(household["wouter"])
    unique_words = " ".join(f"word{number:016d}" for number in range(70_000))
    await _add_file(
        me,
        household["w_personal"],
        "attachments/huge",
        "huge.txt",
        f"Signed rental agreement. {unique_words}",
    )
    results = await search_engrams(me, "rental agreement")
    assert [r["key"] for r in results] == ["attachments/huge"]


async def test_an_engram_of_unique_words_at_the_ceiling_is_stored(tx, household):
    """The worst realistic engram still fits: every word distinct, at full size."""
    me = principal_for(household["wouter"])
    ceiling = get_settings().engrams.max_chars
    words = " ".join(f"é{number:x}" for number in range(40_000))[:ceiling]
    await save_engram(me, "personal", "big.md", words, message="x")
    assert await search_engrams(me, "é1f") != []


@pytest.mark.parametrize("table", ["engrams", "attachments"])
async def test_the_search_vector_is_stored(tx, table):
    """Both tables carry a generated vector column, and no index on it.

    The index is absent on purpose: under row security the planner cannot
    use it for a text match, so it would only slow writes down. See the
    module docstring of ``reef.search``.
    """
    generated = await Engram.raw(
        "SELECT is_generated FROM information_schema.columns "
        "WHERE table_name = {} AND column_name = 'search_vector'",
        table,
    )
    assert [row["is_generated"] for row in generated] == ["ALWAYS"]
    indexes = await Engram.raw(
        "SELECT indexdef FROM pg_indexes "
        "WHERE tablename = {} AND indexdef LIKE '%search_vector%'",
        table,
    )
    assert indexes == []


# --- one missing word no longer rules an engram out --------------------------------


async def _two_boiler_engrams(me) -> None:
    """Save one engram with every word of the query and one with only some.

    :param me: the principal who owns the engrams
    """
    await save_engram(
        me,
        "personal",
        "some.md",
        "The boiler was serviced. The boiler is fine. Boiler, boiler, boiler.",
        message="x",
    )
    await save_engram(
        me,
        "personal",
        "every.md",
        "The boiler is a Vaillant and was serviced in March.",
        message="x",
    )


async def test_an_engram_missing_one_word_of_the_query_is_still_found(tx, household):
    """A question in ordinary words used to find nothing."""
    me = principal_for(household["wouter"])
    await save_engram(
        me,
        "personal",
        "house.md",
        "The boiler is a Vaillant ecoTEC and was serviced in March.",
        message="x",
    )
    results = await search_engrams(me, "which brand is our boiler")
    assert [r["path"] for r in results] == ["house.md"]
    assert "**boiler**" in results[0]["snippet"]


async def test_an_engram_with_every_word_ranks_above_one_with_some(tx, household):
    me = principal_for(household["wouter"])
    await _two_boiler_engrams(me)
    results = await search_engrams(me, "boiler vaillant march")
    assert [r["path"] for r in results] == ["every.md", "some.md"]


async def test_an_excluded_word_keeps_the_query_strict(tx, household):
    """``a or not b`` would match nearly every engram, so it is never built."""
    me = principal_for(household["wouter"])
    await _two_boiler_engrams(me)
    await save_engram(me, "personal", "other.md", "Nothing relevant here.", message="x")
    results = await search_engrams(me, "boiler -vaillant")
    assert [r["path"] for r in results] == ["some.md"]


async def test_a_quoted_phrase_stays_a_phrase(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(
        me, "personal", "apart.md", "Serviced the car. The boiler waits.", message="x"
    )
    await save_engram(
        me, "personal", "together.md", "We serviced the boiler today.", message="x"
    )
    results = await search_engrams(me, '"serviced the boiler"')
    assert [r["path"] for r in results] == ["together.md"]


async def test_a_long_engram_does_not_win_by_length_alone(tx, household):
    """Rank is scaled by length, so repeating a word in a long engram is not enough."""
    me = principal_for(household["wouter"])
    filler = " ".join(f"word{n}" for n in range(3000))
    await save_engram(me, "personal", "long.md", f"boiler boiler {filler}", message="x")
    await save_engram(me, "personal", "short.md", "The boiler manual.", message="x")
    results = await search_engrams(me, "boiler manual warranty")
    assert results[0]["path"] == "short.md"


async def test_a_nul_byte_in_the_query_cannot_error(tx, household):
    me = principal_for(household["wouter"])
    await save_engram(me, "personal", "house.md", "The boiler manual.", message="x")
    assert [r["path"] for r in await search_engrams(me, "boi\x00ler")] == ["house.md"]
    assert await search_engrams(me, "\x00") == []
