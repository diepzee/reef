"""Full-text search over engrams, inside the same RLS session as every read.

The competitive positioning this feature carries: a search that forgets a
filter returns nothing, not somebody else's memories. The security tests
here are the point, not an afterthought.
"""

import pytest

from reef import sections
from reef.access import AccessDenied, Principal, arm
from reef.config import get_settings
from reef.engrams import save_engram
from reef.indexfields import summary
from reef.models import Attachment, AttachmentStatus, Engram
from reef.search import PASSAGE_CHARS, search_engrams

#: A note shaped like the made-up person's subscriptions: long enough to be
#: cut into sections, with the deciding fact late on the line that matches.
SUBSCRIPTIONS = (
    "# Abonnementen\n\n"
    "Wat we elke maand betalen, en wat nog loopt of al stopt.\n\n"
    "## Streaming\n\n"
    "Netflix: standaard met reclame, 6 euro per maand, betaald met de "
    "kredietkaart. Spotify Duo: 17 euro per maand, gedeeld met de partner, "
    "verlengd elke maand zonder einddatum. De prijzen gaan elk voorjaar wat "
    "omhoog, dus in april kijken we ze na en schrappen we wat niemand nog "
    "gebruikt. Wie iets nieuws wil, vraagt het eerst aan de ander, en we "
    "proberen nooit meer dan twee diensten tegelijk te houden. Films huren we "
    "liever los, voor een avond, dan er een dienst bij te nemen voor een "
    "enkele reeks. De rekening komt op de eerste van de maand. "
    "Disney+ is gestopt in juni.\n\n"
    "## Kranten\n\n"
    "Krant De Mosselkoerier: digitaal, 9 euro per maand. "
    "Opgezegd op 1 oktober 2026, loopt af eind oktober.\n"
    "Het tuinblad komt vier keer per jaar en kost 32 euro per jaar, te "
    "betalen in januari. Het weekblad over koken lezen we in de bibliotheek, "
    "dat kost niets. Oude nummers gaan naar de buren, die ze graag lezen bij "
    "het ontbijt. Folders en reclame willen we niet: er hangt een sticker op "
    "de brievenbus, en wat toch komt, gaat meteen bij het oud papier.\n\n"
    "## Sport\n\n"
    "De fitness in het dorp kost 30 euro per maand per persoon, met een "
    "opzegtermijn van een maand. De zwemclub vraagt 120 euro per seizoen, "
    "betaald in september. Het tennisplein huren we per uur, ongeveer tien "
    "keer per zomer, aan 12 euro per uur. Sportkleren kopen we in de solden, "
    "en schoenen pas als de oude echt versleten zijn. De mutualiteit betaalt "
    "een deel van de fitness terug, als we het formulier op tijd binnenbrengen.\n\n"
    "## Software\n\n"
    "De wachtwoordkluis kost 36 euro per jaar, voor het hele gezin. De "
    "opslag voor foto's kost 3 euro per maand, met twee terabyte ruimte. Het "
    "tekstprogramma is een eenmalige aankoop, zonder maandprijs. De "
    "antivirus zit in het besturingssysteem, die kost niets extra. Elk jaar "
    "in december kijken we na wat we nog gebruiken en wat weg mag, en we "
    "noteren hier wat we besloten hebben, zodat we het volgend jaar weten. "
    "Een nieuwe dienst komt er pas bij als er een andere weggaat.\n"
)


#: A sentence that answers nothing, to make a note or a section long.
FILL = "Dit is gewone tekst over het huishouden die niets met de vraag te maken heeft. "

#: The fact the late-match notes hold, after more than a passage of filler.
OMNIUM = "De omnium bij Ethias is opgezegd op 1 oktober 2026."


def piece_of(title: str, body: str, anchor: str | None) -> str:
    """Return one piece of an engram, cut as a passage is.

    :param title: the engram's title
    :param body: its body
    :param anchor: the piece's anchor; None for the whole-engram piece
    :returns: the piece with its title and heading in front, cut to a passage
    """
    pieces = sections.split(title, summary(body), body)
    return next(p.text for p in pieces if p.anchor == anchor)[:PASSAGE_CHARS]


def subscriptions_piece(anchor: str | None, title: str = "Abonnementen") -> str:
    """Return one piece of :data:`SUBSCRIPTIONS` as a passage shows it.

    :param anchor: the piece's anchor; None for the whole-engram piece
    :param title: the title the note was saved under
    :returns: the piece with its title and heading in front, cut to a passage
    """
    return piece_of(title, SUBSCRIPTIONS, anchor)


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


# --- passages without vectors ---------------------------------------------------


async def _subscriptions(me: Principal, title: str = "Abonnementen") -> None:
    """Save the subscriptions note, without vectors.

    :param me: the principal who owns it
    :param title: its title
    """
    await save_engram(
        me,
        "personal",
        "finance/subscriptions.md",
        SUBSCRIPTIONS,
        message="x",
        title=title,
    )


async def test_a_passage_without_vectors_holds_the_rest_of_the_matching_line(
    tx, household
):
    """The snippet stops a few words after the match; the passage does not.

    Seen in a test conversation: the snippet ended before "opgezegd", and the
    assistant told the person the subscription still ran.
    """
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "krant abonnement opgezegd", passages=True)
    assert "1 oktober 2026" not in hit["snippet"]
    assert "1 oktober 2026" in hit["passage"]
    assert len(hit["passage"]) <= PASSAGE_CHARS


async def test_a_passage_without_vectors_is_the_section_that_matched(tx, household):
    """The section holding the matched words, not the first section."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "krant abonnement opgezegd", passages=True)
    assert hit["passage"] == subscriptions_piece("kranten")
    assert "Netflix" not in hit["passage"]


async def test_a_snippet_that_runs_into_the_next_section_finds_its_own(tx, household):
    """A match at the end of a section is still placed in that section.

    Its snippet runs on past the heading after it, so the whole snippet is in
    no one section; the line holding the match is.
    """
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "disney", passages=True)
    assert "Kranten" in hit["snippet"]
    assert hit["passage"] == subscriptions_piece("streaming")


async def test_a_match_only_in_the_title_gets_the_start_of_the_engram(tx, household):
    """The body holds no matched word, so the passage is the whole-engram piece."""
    me = principal_for(household["wouter"])
    await _subscriptions(me, title="Huishoudpot")
    [hit] = await search_engrams(me, "huishoudpot", passages=True)
    assert "**" not in hit["snippet"]
    assert hit["passage"] == subscriptions_piece(None, title="Huishoudpot")


async def test_a_match_in_the_title_line_gets_the_start_of_the_engram(tx, household):
    """The body's "# Abonnementen" line is the title, which no section holds."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "abonnementen", passages=True)
    assert hit["snippet"].startswith("**Abonnementen**")
    assert hit["passage"] == subscriptions_piece(None)


async def test_a_heading_named_in_an_overview_finds_its_own_section(tx, household):
    """The snippet starts at a heading, which no section's text holds.

    The overview names that heading too, but the passage is the section under
    it, not the overview.
    """
    me = principal_for(household["wouter"])
    body = (
        "# Abonnementen\n\nOverzicht: Streaming, Kranten, Sport. "
        + FILL * 3
        + "\n\n## Streaming\n\nNetflix kost 6 euro per maand. "
        + FILL * 6
        + "\n\n## Kranten\n\nOpgezegd: De Mosselkoerier, op 1 oktober 2026. "
        + "Het tuinblad blijft lopen. "
        + FILL * 3
        + "\n\n## Sport\n\n"
        + FILL * 16
    )
    await save_engram(me, "personal", "abo.md", body, message="x", title="Abo")
    [hit] = await search_engrams(me, "kranten opgezegd", passages=True)
    assert hit["snippet"].startswith("**Kranten**")
    assert hit["passage"] == piece_of("Abo", body, "kranten")


async def test_a_heading_word_inside_a_longer_word_does_not_count(tx, household):
    """ "Krantenwinkel" in an earlier section is not the heading "Krant"."""
    me = principal_for(household["wouter"])
    body = (
        "# Huis\n\nWat er in huis gebeurt.\n\n## Boodschappen\n\n"
        "De Krantenwinkel op de hoek verkoopt ook brood en melk. "
        + FILL
        * 4
        + "\n\n## Krant\n\n"
        "De Mosselkoerier: digitaal, 9 euro per maand. Opgezegd op 1 oktober "
        "2026, loopt af eind oktober. Het tuinblad komt vier keer per jaar en "
        "kost 32 euro per jaar. Oude nummers gaan naar de buren, die ze graag "
        "lezen bij het ontbijt, en folders willen we niet.\n\n## Sport\n\n" + FILL * 20
    )
    await save_engram(me, "personal", "huis.md", body, message="x", title="Huis")
    [hit] = await search_engrams(me, "krant", passages=True)
    assert hit["snippet"].startswith("**Krant**")
    assert hit["passage"] == piece_of("Huis", body, "krant")


async def test_a_late_match_in_a_one_piece_engram_is_in_the_passage(tx, household):
    """The passage starts at the paragraph that holds the match, after the title."""
    me = principal_for(household["wouter"])
    body = "\n\n".join([FILL * 4] * 6) + f"\n\n{OMNIUM}"
    whole = sections.split("Auto", summary(body), body)
    assert len(whole) == 1
    assert whole[0].text.index("Ethias") > PASSAGE_CHARS
    await save_engram(me, "personal", "auto.md", body, message="x", title="Auto")
    [hit] = await search_engrams(me, "omnium opgezegd", passages=True)
    assert hit["passage"] == f"Auto\n\n…\n\n{OMNIUM}"


async def test_a_late_match_in_a_long_section_is_in_the_passage(tx, household):
    """The section's title and heading stay in front of the paragraph."""
    me = principal_for(household["wouter"])
    body = (
        "# Auto\n\nAlles over de auto.\n\n## Verzekering\n\n"
        + "\n\n".join([FILL * 4] * 5)
        + f"\n\n{OMNIUM}\n\n## Onderhoud\n\n"
        + FILL * 10
    )
    pieces = sections.split("Auto", summary(body), body)
    insurance = next(p for p in pieces if p.anchor == "verzekering")
    assert insurance.text.index("Ethias") > PASSAGE_CHARS
    await save_engram(me, "personal", "auto.md", body, message="x", title="Auto")
    [hit] = await search_engrams(me, "omnium opgezegd", passages=True)
    assert hit["passage"] == f"Auto\nVerzekering\n\n…\n\n{OMNIUM}"


async def test_a_late_match_on_one_long_line_keeps_the_snippet(tx, household):
    """No paragraph or line start brings the match into a passage."""
    me = principal_for(household["wouter"])
    body = FILL * 24 + OMNIUM
    assert "\n" not in body
    await save_engram(me, "personal", "auto.md", body, message="x", title="Auto")
    [hit] = await search_engrams(me, "omnium opgezegd", passages=True)
    assert "Ethias" in hit["snippet"]
    assert hit["passage"] == hit["snippet"]


async def test_a_quoted_phrase_gets_the_section_too(tx, household):
    """An exact query is never ranked by meaning; its passage is still the section."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, '"de mosselkoerier"', passages=True)
    assert hit["passage"] == subscriptions_piece("kranten")


async def test_without_passages_a_hit_carries_none(tx, household):
    """Only a caller that asks for passages pays for reading the engrams."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "krant abonnement opgezegd")
    assert "passage" not in hit
