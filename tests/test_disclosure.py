"""Who may see whose details, decided by Postgres rather than by the caller.

The rule these cover -- only a cove's owner sees member email addresses --
existed before, as a blanking pass in a web handler that had already fetched
every address. These assert it now holds at the source, so a caller that
forgets gets the safe answer instead of the whole roster.

Row-level security cannot express this on its own: a policy letting
co-members read each other's ``persons`` rows hands over the email column
with everything else. Hence functions with the check inside.
"""

from rif.access import Principal, arm
from rif.db import DB
from rif.models import Person
from rif.rls import AUTHZ_ROLE
from rif.spaces import display_names, member_names, member_roster, space_owner


async def _arm(person) -> None:
    """Bind ``person`` as the RLS principal for the current transaction.

    :param person: the person to arm
    """
    await arm(Principal(person_id=person.id, email=person.email))


async def test_the_disclosure_functions_are_owned_by_the_bypassing_role():
    """They read persons for people the caller cannot see, so they must bypass."""
    rows = await DB._run_in_new_connection(
        "SELECT p.proname, pg_get_userbyid(p.proowner) AS owner, p.prosecdef, "
        "p.proconfig FROM pg_proc p WHERE p.proname IN "
        "('rif_roster', 'rif_space_owner', 'rif_display_names', "
        "'rif_person_id_by_email', 'rif_invites_minted', 'rif_oldest_invite', "
        "'rif_member_faces', 'rif_member_avatar')"
    )
    assert len(rows) == 8
    for row in rows:
        assert row["owner"] == AUTHZ_ROLE, row["proname"]
        assert row["prosecdef"] is True, row["proname"]
        assert "search_path=public, pg_catalog" in (row["proconfig"] or [])


async def test_an_owner_sees_member_emails(tx, household):
    """The owner administers the cove, so removal needs addresses."""
    await _arm(household["wouter"])  # owns the shared space
    roster = await member_roster(household["shared"].id)

    assert sorted(m["display_name"] for m in roster) == ["Partner", "Wouter"]
    assert all(member["email"] for member in roster), roster


async def test_a_plain_member_sees_names_but_no_emails(tx, household):
    """The backstop: the address never leaves the database for a non-owner."""
    await _arm(household["partner"])  # a member, not the owner
    roster = await member_roster(household["shared"].id)

    assert sorted(m["display_name"] for m in roster) == ["Partner", "Wouter"]
    assert [member["email"] for member in roster] == ["", ""]


async def test_a_non_member_sees_no_roster_at_all(tx, household, graph):
    """Not a redaction -- a stranger learns nothing, not even who is in it."""
    stranger = await graph.person("stranger@example.test", "Stranger")
    await _arm(stranger)

    assert await member_roster(household["shared"].id) == []
    assert await member_names(household["shared"].id) == []


async def test_an_unarmed_caller_sees_no_roster(tx, household):
    """Fail closed when no principal is bound at all."""
    assert await member_roster(household["shared"].id) == []


async def test_every_member_sees_the_owners_address(tx, household):
    """Deliberately kept: the owner is the cove's accountable contact."""
    await _arm(household["partner"])
    owner = await space_owner(household["shared"].id)

    assert owner == {"display_name": "Wouter", "email": "wouter@example.test"}


async def test_a_non_member_cannot_see_the_owner(tx, household, graph):
    """That contract stops at the cove's edge."""
    stranger = await graph.person("outsider@example.test", "Outsider")
    await _arm(stranger)
    assert await space_owner(household["shared"].id) is None


async def test_display_names_resolve_without_exposing_addresses(tx, household):
    """Revision authorship keeps rendering names, and only names."""
    await _arm(household["partner"])
    names = await display_names([household["wouter"].id, household["partner"].id])

    assert names == {
        household["wouter"].id: "Wouter",
        household["partner"].id: "Partner",
    }


async def test_display_names_ignores_ids_that_do_not_exist(tx, household):
    """A stale author id maps to nothing rather than raising."""
    from uuid import uuid4

    names = await display_names([uuid4()])
    assert names == {}


#: Stand-in picture bytes. Not a real PNG -- nothing here decodes one, and
#: these functions are about who may read the bytes, not what they contain.
FACE = b"\x89PNG-pretend"


async def _set_avatar(seed, person, raw: bytes = FACE) -> None:
    """Give ``person`` a stored picture, as pre-existing state.

    Through ``seed`` rather than the ORM: ``persons`` is self-only under
    ``FORCE ROW LEVEL SECURITY``, so an update armed as anybody but the
    subject silently matches no rows, and the tests below would then assert
    against a picture that was never stored.

    :param seed: the seeding connection
    :param person: the person to give a picture to
    :param raw: the bytes to store
    """
    await seed.execute(
        "UPDATE persons SET avatar_mime = 'image/png', avatar_bytes = $1 WHERE id = $2",
        raw,
        person.id,
    )


async def test_a_member_sees_a_co_members_picture(tx, household, seed):
    """The whole point: faces are shared with the people who share the cove."""
    await _set_avatar(seed, household["wouter"])
    await _arm(household["partner"])

    rows = await Person.raw(
        "SELECT * FROM rif_member_avatar({}, {})",
        household["shared"].id,
        household["wouter"].id,
    )
    assert len(rows) == 1
    assert bytes(rows[0]["avatar_bytes"]) == FACE
    assert rows[0]["avatar_mime"] == "image/png"


async def test_a_non_member_cannot_see_a_picture(tx, household, graph, seed):
    """A stranger asking about a cove they are not in gets nothing."""
    await _set_avatar(seed, household["wouter"])
    stranger = await graph.person("stranger@example.test", "Stranger")
    await _arm(stranger)

    rows = await Person.raw(
        "SELECT * FROM rif_member_avatar({}, {})",
        household["shared"].id,
        household["wouter"].id,
    )
    assert rows == []


async def test_a_cove_is_not_a_lookup_oracle_over_every_account(
    tx, household, graph, seed
):
    """Naming a cove you *are* in does not fetch the face of somebody outside it.

    Without the "target is a member too" half of the rule, any membership
    anywhere would turn into a way to pull any person's picture by id.
    """
    outsider = await graph.person("outsider@example.test", "Outsider")
    await _set_avatar(seed, outsider)
    await _arm(household["partner"])  # a real member of the shared cove

    rows = await Person.raw(
        "SELECT * FROM rif_member_avatar({}, {})",
        household["shared"].id,
        outsider.id,
    )
    assert rows == []


async def test_an_unarmed_caller_sees_no_picture(tx, household, seed):
    """Fail closed when no principal is bound at all."""
    await _set_avatar(seed, household["wouter"])
    rows = await Person.raw(
        "SELECT * FROM rif_member_avatar({}, {})",
        household["shared"].id,
        household["wouter"].id,
    )
    assert rows == []


async def test_the_roster_reports_who_has_a_picture(tx, household, seed):
    """``avatar_len`` is what tells the UI to draw a face or an initial."""
    await _set_avatar(seed, household["wouter"])
    await _arm(household["partner"])
    roster = await member_roster(household["shared"].id)

    sizes = {member["display_name"]: member["avatar_len"] for member in roster}
    assert sizes == {"Wouter": len(FACE), "Partner": None}


async def test_a_non_member_learns_no_face_sizes(tx, household, graph, seed):
    """The roster's picture column stops at the cove's edge like the rest of it."""
    await _set_avatar(seed, household["wouter"])
    stranger = await graph.person("nobody@example.test", "Nobody")
    await _arm(stranger)

    rows = await Person.raw(
        "SELECT * FROM rif_member_faces({})", household["shared"].id
    )
    assert rows == []
