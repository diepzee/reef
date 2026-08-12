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
        "'rif_person_id_by_email', 'rif_invites_minted', 'rif_oldest_invite')"
    )
    assert len(rows) == 6
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
