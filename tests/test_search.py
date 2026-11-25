"""Full-text search over engrams, inside the same RLS session as every read.

The competitive positioning this feature carries: a search that forgets a
filter returns nothing, not somebody else's memories. The security tests
here are the point, not an afterthought.
"""

import random
import re
from uuid import uuid4

import pytest

from reef import search, sections
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


def plain(text: str) -> str:
    """Return a text without asterisks and with its white space collapsed.

    :param text: a snippet or a passage
    :returns: the text as the two are compared
    """
    return " ".join(text.replace("*", "").split())


def untagged(text: str) -> str:
    """Return a text with a space where each HTML-like tag stood.

    Postgres writes a snippet that way, so a snippet is compared with a
    passage without its tags.

    :param text: a passage
    :returns: the text without tags
    """
    return re.sub(r"<[^<>]*>", " ", text)


def holds(passage: str, snippet: str) -> bool:
    """Tell whether a passage holds a snippet's text in full.

    :param passage: a passage
    :param snippet: the hit's snippet
    :returns: whether the snippet's text is in the passage, as written or
        with a space where each tag stood
    """
    wanted = plain(snippet)
    return wanted in plain(passage) or wanted in plain(untagged(passage))


def assert_holds_its_snippet(hit: dict) -> None:
    """Assert what every passage by words keeps: never less than the snippet.

    :param hit: a search hit with a passage
    """
    passage, snippet = hit["passage"], hit["snippet"]
    if passage != snippet:
        assert len(passage) <= PASSAGE_CHARS
        assert holds(passage, snippet)


def widened(header: str, body: str, begin: int) -> str:
    """Return a passage widened from a place in a body.

    :param header: the title and the nearest heading above, one per line
    :param body: the engram's body
    :param begin: where in the stripped body the shown text starts
    :returns: the passage as search gives it
    """
    return f"{header}\n\n…\n\n{body.strip()[begin:]}"[:PASSAGE_CHARS]


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
    assert_holds_its_snippet(hit)


async def test_a_match_near_the_start_gets_the_start_of_the_engram(tx, household):
    """The snippet ends within a passage's length of the start: the start it is."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "krant abonnement opgezegd", passages=True)
    assert hit["passage"] == subscriptions_piece(None)
    assert "Opgezegd op 1 oktober 2026" in hit["passage"]


async def test_a_snippet_that_runs_into_the_next_section_is_kept_whole(tx, household):
    """A snippet that runs on past a heading is shown as it runs."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "disney", passages=True)
    assert "Kranten" in hit["snippet"]
    assert hit["passage"] == subscriptions_piece(None)
    assert_holds_its_snippet(hit)


async def test_a_match_only_in_the_title_gets_the_start_of_the_engram(tx, household):
    """The body holds no matched word, so the passage is the whole-engram piece."""
    me = principal_for(household["wouter"])
    await _subscriptions(me, title="Huishoudpot")
    [hit] = await search_engrams(me, "huishoudpot", passages=True)
    assert "**" not in hit["snippet"]
    assert hit["passage"] == subscriptions_piece(None, title="Huishoudpot")


async def test_a_match_in_the_title_line_gets_the_start_of_the_engram(tx, household):
    """The body's "# Abonnementen" line opens the engram."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "abonnementen", passages=True)
    assert hit["snippet"].startswith("**Abonnementen**")
    assert hit["passage"] == subscriptions_piece(None)


async def test_a_snippet_at_a_heading_an_overview_names_keeps_its_fact(tx, household):
    """The snippet starts at the "Kranten" heading, not at the overview naming it."""
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
    assert hit["passage"] == piece_of("Abo", body, None)
    assert "De Mosselkoerier, op 1 oktober 2026" in hit["passage"]


async def test_a_longer_word_holding_the_match_does_not_move_the_passage(tx, household):
    """ "Krantenwinkel" comes earlier; the passage holds what follows "Krant"."""
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
    assert hit["passage"] == piece_of("Huis", body, None)
    assert "Opgezegd op 1 oktober 2026" in hit["passage"]


#: The head of a Markdown table of costs.
TABLE = "| Wat | Bedrag |\n| --- | --- |\n"

#: The fact the trips' second "Kosten" holds.
ITALY = "Het hotel in Rome kostte 900 euro. "


def trips(norway: str, italy: str, filler: int = 14) -> str:
    """Return a note with two trips, each with a section called "Kosten".

    :param norway: the first "Kosten" text
    :param italy: the second "Kosten" text
    :param filler: how many filler sentences follow the first
    :returns: the body
    """
    return (
        "# Reizen\n\nOnze reizen.\n\n## Noorwegen\n\n"
        + FILL * 3
        + f"\n\n## Kosten\n\n{norway}"
        + FILL * filler
        + "\n\n## Italie\n\n"
        + FILL * 3
        + f"\n\n## Kosten\n\n{italy}"
        + FILL * 14
    )


@pytest.mark.parametrize(
    ("norway", "italy"),
    [
        ("De vlucht kostte 300 euro. ", ITALY),
        (
            TABLE + "| Vlucht | 300 euro |\n| Hotel in Oslo | 700 euro |\n\n",
            TABLE + "| Hotel in Rome | 900 euro |\n\n",
        ),
        (
            "- Verzekering: 40 euro\n- Vlucht naar Oslo: 300 euro\n\n",
            "- Verzekering: 40 euro\n- Hotel in Rome: 900 euro\n\n",
        ),
    ],
    ids=["sentence", "same-table-head", "same-first-item"],
)
async def test_a_repeated_heading_shows_the_place_the_snippet_came_from(
    tx, household, norway, italy
):
    """Two sections called "Kosten": the passage starts at the later one.

    It is past a passage's length, so the passage starts at the paragraph
    that holds the snippet: the "## Kosten" line. "## Italie" is at the same
    level, so its section ended there, and no heading is put in front.
    """
    me = principal_for(household["wouter"])
    body = trips(norway, italy)
    await save_engram(me, "personal", "reizen.md", body, message="x", title="Reizen")
    [hit] = await search_engrams(me, "kosten hotel rome", passages=True)
    assert hit["snippet"].startswith("**Kosten**")
    begin = body.strip().rindex("## Kosten")
    assert hit["passage"] == widened("Reizen", body, begin)
    assert "900 euro" in hit["passage"]


@pytest.mark.parametrize(
    "norway",
    [
        "De vlucht kostte 300 euro. "
        + FILL * 4
        + "De kosten voor eten vielen mee. "
        + FILL * 4
        + "Het hotel in Bergen was klein. "
        + FILL * 4
        + "Volgend jaar Rome? ",
        "De vlucht kostte 300 euro. "
        + FILL * 4
        + "Kosten voor eten vielen mee. "
        + FILL * 4
        + "Het hotel in Bergen was klein. ",
    ],
    ids=["A1-every-word-spread", "A3-heading-word-in-prose"],
)
async def test_an_earlier_same_named_section_with_the_words_is_not_shown(
    tx, household, norway
):
    """The first "Kosten" holds the query's words too, spread through its prose.

    Ranking same-named sections by the words they hold chose it, and lost
    "900 euro". The snippet is where Postgres found the words together.
    """
    me = principal_for(household["wouter"])
    body = trips(norway, ITALY, filler=6)
    await save_engram(me, "personal", "reizen.md", body, message="x", title="Reizen")
    [hit] = await search_engrams(me, "kosten hotel rome", passages=True)
    assert "900 euro" in hit["passage"]
    assert_holds_its_snippet(hit)
    begin = body.strip().rindex("## Kosten")
    assert hit["passage"] == widened("Reizen", body, begin)


async def test_a_repeated_subsection_with_the_words_in_prose_is_not_shown(
    tx, household
):
    """Two "Contract" subsections; the first says "contract" and "opgezegd" apart."""
    me = principal_for(household["wouter"])
    body = (
        "# Abonnementen\n\nWat we betalen.\n\n## Telenet\n\n"
        + FILL * 2
        + "\n\n### Contract\n\nLoopt tot eind 2027. "
        + FILL * 4
        + "Het contract wordt elk jaar stilzwijgend verlengd. "
        + FILL * 4
        + "Nog niet opgezegd. "
        + FILL * 4
        + "\n\n## Proximus\n\n"
        + FILL * 2
        + "\n\n### Contract\n\nOpgezegd op 1 oktober 2026, bevestigd per brief. "
        + FILL * 12
    )
    await save_engram(me, "personal", "abo.md", body, message="x", title="Abonnementen")
    [hit] = await search_engrams(me, "contract opgezegd", passages=True)
    assert "Opgezegd op 1 oktober 2026" in hit["passage"]
    assert_holds_its_snippet(hit)
    begin = body.strip().rindex("### Contract")
    assert hit["passage"] == widened("Abonnementen\nProximus", body, begin)


@pytest.mark.parametrize(
    "planning",
    [
        "We willen naar Rome. " + FILL * 4 + "Een hotel zoeken we nog. " + FILL * 8,
        FILL * 10,
    ],
    ids=["E1-planning-holds-both-words", "E2-baseline"],
)
async def test_a_snippet_across_a_heading_keeps_what_follows_it(
    tx, household, planning
):
    """One section ends with "Rome", the next opens with "hotel": the snippet holds both.

    No one section holds the snippet, and an earlier one may hold both words
    apart. The passage shows the snippet as it runs, with "900 euro".
    """
    me = principal_for(household["wouter"])
    body = (
        f"# Reizen\n\nIntro.\n\n## Planning\n\n{planning}"
        + "\n\n## Vervoer\n\n"
        + FILL * 6
        + "Met de trein naar Rome."
        + "\n\n## Verblijf\n\nHet hotel kostte 900 euro. "
        + FILL * 14
    )
    await save_engram(me, "personal", "reizen.md", body, message="x", title="Reizen")
    [hit] = await search_engrams(me, "rome hotel", passages=True)
    assert "## Verblijf" in hit["snippet"]
    assert "Het hotel kostte 900 euro." in hit["passage"]
    assert_holds_its_snippet(hit)


@pytest.mark.parametrize(
    "opening", ["", "Bijgehouden door Wouter.\n"], ids=["plain", "same-opening"]
)
async def test_a_repeated_subheading_shows_the_place_the_snippet_came_from(
    tx, household, opening
):
    """Two "Notities" under different years: the passage starts at the later one."""
    me = principal_for(household["wouter"])
    body = (
        "# Huis\n\nWat er in huis gebeurt.\n\n## 2025\n\n"
        + FILL * 3
        + f"\n\n### Notities\n\n{opening}De oude ketel werd nagekeken in mei. "
        + FILL * 14
        + "\n\n## 2026\n\n"
        + FILL * 3
        + f"\n\n### Notities\n\n{opening}"
        + "De nieuwe Daikin warmtepomp is geplaatst op 3 maart. "
        + FILL * 14
    )
    await save_engram(me, "personal", "huis.md", body, message="x", title="Huis")
    [hit] = await search_engrams(me, "notities daikin", passages=True)
    assert hit["snippet"].startswith("**Notities**")
    begin = body.strip().rindex("### Notities")
    assert hit["passage"] == widened("Huis\n2026", body, begin)
    assert "geplaatst op 3 maart" in hit["passage"]


async def test_a_match_after_an_underscore_is_found(tx, household):
    """Postgres reads "max_connections" as two words; so does the passage."""
    me = principal_for(household["wouter"])
    body = (
        "# Config\n\nInstellingen.\n\n## Server\n\n"
        + FILL * 4
        + "\n\n## Database\n\nZet max_connections op 200 en herstart de dienst "
        "daarna meteen. " + FILL * 14 + "\n\n## Overig\n\n" + FILL * 10
    )
    await save_engram(me, "personal", "config.md", body, message="x", title="Config")
    [hit] = await search_engrams(me, "connections", passages=True)
    assert hit["snippet"].startswith("**connections**")
    assert hit["passage"] == piece_of("Config", body, None)
    assert "max_connections op 200" in hit["passage"]


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
    """The section's heading stays in front, and the text runs on after it."""
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
    begin = body.strip().index(OMNIUM)
    assert hit["passage"] == widened("Auto\nVerzekering", body, begin)
    assert hit["passage"].startswith(f"Auto\nVerzekering\n\n…\n\n{OMNIUM}")


async def test_a_late_match_on_one_long_line_keeps_the_snippet(tx, household):
    """No paragraph or line start brings the match into a passage."""
    me = principal_for(household["wouter"])
    body = FILL * 24 + OMNIUM
    assert "\n" not in body
    await save_engram(me, "personal", "auto.md", body, message="x", title="Auto")
    [hit] = await search_engrams(me, "omnium opgezegd", passages=True)
    assert "Ethias" in hit["snippet"]
    assert hit["passage"] == hit["snippet"]


#: Paragraphs that push what follows them past a passage's length.
LONG = "\n\n".join([FILL * 4] * 6)

#: A line whose deciding fact follows the few words a snippet holds.
LATE = (
    "Krant De Mosselkoerier: digitaal, 9 euro per maand. "
    "Opgezegd op 1 oktober 2026, loopt af eind oktober."
)


@pytest.mark.parametrize(
    ("blank", "end"),
    [("\n\n", "\n"), ("\r\n\r\n", "\r\n"), ("\n \t \n", "\n")],
    ids=["lf", "crlf", "blank-line-holding-spaces"],
)
async def test_a_late_match_in_a_paragraph_that_fits_starts_at_the_paragraph(
    tx, household, blank, end
):
    """The paragraph's first line comes along, whatever ends its lines.

    A blank line may hold spaces, or the carriage return of a Windows line end.
    """
    me = principal_for(household["wouter"])
    body = blank.join(
        [
            "# Abo",
            *[FILL * 4] * 6,
            "## Kranten",
            f"Eerste regel van de alinea.{end}{LATE}",
        ]
    )
    await save_engram(me, "personal", "abo.md", body + end, message="x", title="Abo")
    [hit] = await search_engrams(me, "krant opgezegd", passages=True)
    assert hit["snippet"].startswith("**Krant** De Mosselkoerier")
    begin = body.index("Eerste regel")
    assert hit["passage"] == widened("Abo\nKranten", body, begin)
    assert hit["passage"].endswith(f"Eerste regel van de alinea.{end}{LATE}")


async def test_a_long_paragraph_shows_the_line_that_holds_the_match(tx, household):
    """Its start would push the match past the cut, so the passage starts at the line."""
    me = principal_for(household["wouter"])
    body = "\n".join([FILL * 2] * 12) + f"\n{LATE}"
    await save_engram(me, "personal", "abo.md", body, message="x", title="Abo")
    [hit] = await search_engrams(me, "krant opgezegd", passages=True)
    assert hit["passage"] == f"Abo\n\n…\n\n{LATE}"


async def test_a_heading_that_repeats_the_title_is_not_shown(tx, household):
    """The nearest heading above repeats the title; an earlier one is not shown.

    "## Kosten" ended where "# Reizen" starts, so it does not hold the text.
    """
    me = principal_for(household["wouter"])
    body = (
        f"## Kosten\n\n{LONG}\n\n# Reizen\n\nOnze reis naar Italie, in het kort."
        "\n\nHet hotel in Rome kostte 900 euro."
    )
    await save_engram(me, "personal", "reizen.md", body, message="x", title="Reizen")
    [hit] = await search_engrams(me, "hotel rome", passages=True)
    assert hit["passage"] == (
        "Reizen\n\n…\n\nOnze reis naar Italie, in het kort.\n\n"
        "Het hotel in Rome kostte 900 euro."
    )


async def test_a_heading_that_opens_the_passage_ends_the_sections_above(tx, household):
    """The shown text starts at "# Reizen", which no heading above holds."""
    me = principal_for(household["wouter"])
    body = f"## Kosten\n\n{LONG}\n\n# Reizen\n\nHet hotel in Rome kostte 900 euro."
    await save_engram(me, "personal", "reizen.md", body, message="x", title="Reizen")
    [hit] = await search_engrams(me, "hotel rome", passages=True)
    assert hit["passage"] == (
        "Reizen\n\n…\n\n# Reizen\n\nHet hotel in Rome kostte 900 euro."
    )


async def test_a_snippet_that_lost_a_tag_is_still_widened(tx, household):
    """Postgres puts a space where an HTML-like tag was; the passage keeps the tag."""
    me = principal_for(household["wouter"])
    body = "De <b>krant</b> is opgezegd op 1 oktober 2026, zegt de brief."
    await save_engram(me, "personal", "brief.md", body, message="x", title="Brief")
    [hit] = await search_engrams(me, "krant", passages=True)
    assert "<b>" not in hit["snippet"]
    assert hit["passage"] == piece_of("Brief", body, None)
    assert "<b>krant</b>" in hit["passage"]
    assert_holds_its_snippet(hit)


#: A table row whose deciding fact stands behind a ``<br>``, further on than
#: the few words a snippet holds.
ROW = (
    "| De Mosselkoerier | krant, digitaal<br>9 euro per maand, betaald via "
    "domiciliering bij de bank van het gezin sinds drie jaar en nooit eerder "
    "aangepast in prijs of looptijd<br>opgezegd op 1 oktober 2026 |"
)


async def test_a_table_cell_with_a_line_break_tag_keeps_its_fact(tx, household):
    """The row holds ``<br>``, as Markdown table cells do: the passage is the
    row, not the few words of the snippet. Seen in a probe, where the snippet
    lost "1 oktober 2026" (RUG-71)."""
    me = principal_for(household["wouter"])
    body = f"# Abonnementen\n\n{LONG}\n\n| Wat | Details |\n|---|---|\n{ROW}\n"
    await save_engram(me, "personal", "abo.md", body, message="x", title="Abonnementen")
    [hit] = await search_engrams(me, "mosselkoerier krant", passages=True)
    assert "<br>" not in hit["snippet"]
    assert "1 oktober 2026" not in hit["snippet"]
    assert hit["passage"] != hit["snippet"]
    assert ROW in hit["passage"]
    assert len(hit["passage"]) <= PASSAGE_CHARS
    assert_holds_its_snippet(hit)


async def test_a_snippet_that_is_not_in_the_body_is_the_passage(tx, household):
    """A tag that holds ``>`` inside quotes is one tag to Postgres. Reef ends
    it at the first ``>``, so a snippet that runs over it is not found and is
    kept as it is."""
    me = principal_for(household["wouter"])
    body = 'De brief over de <a href="x>y">krant</a> is opgezegd op 1 oktober 2026.'
    await save_engram(me, "personal", "brief.md", body, message="x", title="Brief")
    [hit] = await search_engrams(me, "brief krant", passages=True)
    assert "href" not in hit["snippet"]
    assert plain(hit["snippet"]).startswith("brief over de krant")
    assert hit["passage"] == hit["snippet"]


async def test_a_quoted_phrase_gets_a_passage_too(tx, household):
    """An exact query is never ranked by meaning; its snippet is still widened."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, '"de mosselkoerier"', passages=True)
    assert hit["passage"] == subscriptions_piece(None)
    assert "Opgezegd op 1 oktober 2026" in hit["passage"]


# Shapes on which choosing a section by its words was a guess. The passage
# holds what Postgres's own snippet holds, wherever that is.


async def test_when_two_sections_hold_every_word_the_snippet_decides(tx, household):
    """Both "Notes" sections hold every word of the query; the snippet is in one."""
    me = principal_for(household["wouter"])
    body = (
        "# Contracts\n\nBoth providers.\n\n## Telenet\n\n"
        + FILL * 3
        + "\n\n### Notes\n\nThe contract runs until 2027. "
        + FILL * 4
        + "We cancelled nothing yet. "
        + FILL * 6
        + "\n\n## Proximus\n\n"
        + FILL * 3
        + "\n\n### Notes\n\nHet contract is opgezegd; cancelled on 1 October 2026. "
        + FILL * 12
    )
    await save_engram(me, "personal", "c.md", body, message="x", title="Contracts")
    [hit] = await search_engrams(me, "notes contract cancelled", passages=True)
    assert "1 October" in hit["snippet"]
    assert hit["passage"] != hit["snippet"]
    assert_holds_its_snippet(hit)


async def test_when_every_section_holds_the_word_the_snippet_decides(tx, household):
    """Each section says the word once; the passage holds the one the snippet holds."""
    me = principal_for(household["wouter"])
    body = (
        "# Huis\n\nIntro.\n\n## A\n\n"
        + FILL * 3
        + "De warmtepomp bromt. "
        + FILL * 10
        + "\n\n## B\n\n"
        + FILL * 3
        + "De warmtepomp is stil. "
        + FILL * 10
        + "\n\n## C\n\n"
        + FILL * 3
        + "De warmtepomp is nieuw. "
        + FILL * 10
    )
    await save_engram(me, "personal", "huis.md", body, message="x", title="Huis")
    [hit] = await search_engrams(me, "warmtepomp", passages=True)
    assert hit["passage"] != hit["snippet"]
    assert_holds_its_snippet(hit)


async def test_when_a_hundred_sections_hold_the_word_the_snippet_decides(tx, household):
    """A hundred sections say "Rome."; Postgres's snippet is in the first."""
    me = principal_for(household["wouter"])
    body = (
        "# Groot\n\nIntro.\n\n"
        + "".join(
            f"## Deel {i}\n\nWe gaan naar Rome. " + FILL * 23 + "\n\n"
            for i in range(100)
        )
        + "## Slot\n\nMet de trein naar Rome.\n\n## Verblijf\n\n"
        + "Het hotel kostte 900 euro. "
        + FILL * 5
    )
    await save_engram(me, "personal", "groot.md", body, message="x", title="Groot")
    [hit] = await search_engrams(me, "rome hotel", passages=True)
    assert hit["passage"] != hit["snippet"]
    assert_holds_its_snippet(hit)


#: Words for generated bodies, some of them matched by the queries below.
WORDS = [
    "krant",
    "opgezegd",
    "Rome",
    "hotel",
    "**vet**",
    "café",
    "max_conn",
    "2026",
    "x",
]

#: What separates them: mostly a space, sometimes a line, paragraph or heading.
SEPARATORS = [" "] * 40 + [
    "\n",
    "\n\n",
    "  ",
    " * ",
    "\n\n## Kop\n\n",
    "\n\n### Sub\n\n",
]


async def test_every_passage_by_words_holds_its_snippet(tx, household):
    """Generated engrams of every size: each passage is never less than its snippet."""
    me = principal_for(household["wouter"])
    rng = random.Random(64)
    for number in range(12):
        size = rng.choice([20, 200, 600, 2_000])
        body = "".join(rng.choice(WORDS) + rng.choice(SEPARATORS) for _ in range(size))
        await save_engram(me, "personal", f"g{number}.md", body, message="x")
    for query in ("krant", "rome hotel", "opgezegd 2026", '"max conn"', "vet café"):
        for hit in await search_engrams(me, query, limit=20, passages=True):
            assert_holds_its_snippet(hit)


def test_a_passage_by_words_never_holds_less_than_its_snippet():
    """Generated bodies and snippets: a passage holds the snippet, or is none."""
    rng = random.Random(7)
    words = [*WORDS, "日本語", "👨‍👩‍👧", "a**b", "**", "#", "-", "l'abo"]
    spaces = [*SEPARATORS, "\t", " ", "\r\n", "\n```\n", "\n\n# Titel\n\n"]
    for _ in range(3_000):
        body = "".join(
            rng.choice(words) + rng.choice(spaces) for _ in range(rng.randint(1, 600))
        )
        title = rng.choice(["", "Titel", "T" * rng.randint(1, 300)])
        flat = plain(body).split(" ")
        first = rng.randrange(len(flat))
        snippet = rng.choice([" ", "\n", "\n\n"]).join(
            f"**{word}**" if rng.random() < 0.3 else word
            for word in flat[first : first + rng.randint(1, 25)]
        )
        if rng.random() < 0.1:
            snippet = rng.choice(["not in the body", "", "<b>krant</b>"])
        row = {"id": uuid4(), "title": title, "description": None, "body": body}
        passage = search._passages({}, [row], {row["id"]: snippet}).get(row["id"])
        if passage is not None:
            assert len(passage) <= PASSAGE_CHARS
            assert plain(snippet) and plain(snippet) in plain(passage)


#: Tags as notes hold them, and text that only looks like one.
TAGS = ["<br>", "<br/>", "</td><td>", "<b>", "</b>", '<a href="x">', "<!-- n -->"]


async def test_every_passage_by_words_holds_its_snippet_around_tags(tx, household):
    """Generated engrams with HTML-like tags between the words: each passage
    is never less than its snippet, and tags do not stop the widening."""
    me = principal_for(household["wouter"])
    rng = random.Random(71)
    for number in range(12):
        size = rng.choice([20, 200, 600, 2_000])
        body = "".join(
            rng.choice(WORDS) + rng.choice([*SEPARATORS, *TAGS, "<", " > ", " < "])
            for _ in range(size)
        )
        await save_engram(me, "personal", f"t{number}.md", body, message="x")
    widened_past_a_tag = 0
    for query in ("krant", "rome hotel", "opgezegd 2026", '"max conn"', "vet café"):
        for hit in await search_engrams(me, query, limit=20, passages=True):
            assert_holds_its_snippet(hit)
            if hit["passage"] != hit["snippet"]:
                widened_past_a_tag += plain(hit["snippet"]) not in plain(hit["passage"])
    assert widened_past_a_tag


def test_a_passage_by_words_never_holds_less_than_its_snippet_around_tags():
    """Generated bodies with tags, and snippets as Postgres writes them (a
    space for each tag): a passage holds the snippet, or is none."""
    rng = random.Random(71)
    spaces = [*SEPARATORS, *TAGS, "\t", "\r\n", "<", ">", "<<", "<>", " < ", "\n<br>\n"]
    found = 0
    for _ in range(3_000):
        body = "".join(
            rng.choice(WORDS) + rng.choice(spaces) for _ in range(rng.randint(1, 600))
        )
        title = rng.choice(["", "Titel", "T" * rng.randint(1, 300)])
        flat = plain(untagged(body)).split(" ")
        first = rng.randrange(len(flat))
        snippet = " ".join(
            f"**{word}**" if rng.random() < 0.3 else word
            for word in flat[first : first + rng.randint(1, 25)]
        )
        row = {"id": uuid4(), "title": title, "description": None, "body": body}
        passage = search._passages({}, [row], {row["id"]: snippet}).get(row["id"])
        if passage is not None:
            found += 1
            assert len(passage) <= PASSAGE_CHARS
            assert plain(snippet) and holds(passage, snippet)
    assert found > 2_000


async def test_without_passages_a_hit_carries_none(tx, household):
    """Only a caller that asks for passages pays for reading the engrams."""
    me = principal_for(household["wouter"])
    await _subscriptions(me)
    [hit] = await search_engrams(me, "krant abonnement opgezegd")
    assert "passage" not in hit
