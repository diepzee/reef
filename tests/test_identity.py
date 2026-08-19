"""Identity resolution through the narrow definer functions.

These cover the one path that necessarily runs before a principal exists.
The properties worth holding: it can fetch a row only by exact key, it
returns only the three columns a principal is built from, and binding a
provider subject is atomic so two racing first sign-ins cannot both win.
"""

import asyncio

from reef.db import DB
from reef.identity import (
    bind_subject,
    person_by_email,
    person_by_subject,
    person_exists,
)
from reef.models import Person
from reef.rls import AUTHZ_ROLE

_FUNCTIONS = (
    "rif_person_by_subject",
    "rif_person_by_email",
    "rif_person_bind",
    "rif_person_alive",
)


async def test_the_identity_functions_are_owned_by_the_bypassing_role():
    """They run before arming, so they must not be subject to policies."""
    rows = await DB._run_in_new_connection(
        "SELECT p.proname, pg_get_userbyid(p.proowner) AS owner, r.rolbypassrls, "
        "p.prosecdef, p.proconfig FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
        f"WHERE p.proname IN {_FUNCTIONS}"
    )
    assert {row["proname"] for row in rows} == set(_FUNCTIONS)
    for row in rows:
        assert row["owner"] == AUTHZ_ROLE
        assert row["rolbypassrls"] is True
        assert row["prosecdef"] is True
        assert "search_path=public, pg_catalog" in (row["proconfig"] or [])


async def test_they_are_closed_to_public():
    """Only the app role may call them."""
    for name in _FUNCTIONS:
        signature = {
            "rif_person_by_subject": "rif_person_by_subject(text)",
            "rif_person_by_email": "rif_person_by_email(text)",
            "rif_person_bind": "rif_person_bind(text, text)",
            "rif_person_alive": "rif_person_alive(uuid)",
        }[name]
        rows = await DB._run_in_new_connection(
            f"SELECT has_function_privilege('public', '{signature}', 'EXECUTE') AS pub"
        )
        assert rows[0]["pub"] is False, f"{name} is callable by PUBLIC"


async def test_lookup_by_subject_returns_only_the_principal_columns(tx, graph):
    """Never the whole row: subject and anything added later stay behind."""
    person = await graph.person(
        "bysubject@example.test", "By Subject", subject="auth0|abc"
    )

    rows = await Person.raw("SELECT * FROM rif_person_by_subject({})", "auth0|abc")
    assert set(rows[0].keys()) == {
        "person_id",
        "person_email",
        "person_display_name",
    }

    identity = await person_by_subject("auth0|abc")
    assert identity.person_id == person.id
    assert identity.email == "bysubject@example.test"
    assert identity.display_name == "By Subject"


async def test_an_unknown_subject_resolves_to_nothing(tx, graph):
    """Fail closed rather than fall back to some other row."""
    await graph.person("known@example.test", "Known")
    assert await person_by_subject("auth0|never-seen") is None


async def test_lookup_by_email_normalises_case(tx, graph):
    """Providers vary the case of a verified address; invitations lowercase it."""
    person = await graph.person("mixed@example.test", "Mixed")
    identity = await person_by_email("MiXeD@ExAmPlE.TeSt")
    assert identity is not None
    assert identity.person_id == person.id


async def test_binding_a_subject_claims_an_unbound_invitation(tx, graph):
    """First sign-in: the invited row gains the provider's subject."""
    person = await graph.person("invited@example.test", "Invited")
    identity = await bind_subject("invited@example.test", "auth0|new")

    assert identity is not None
    assert identity.person_id == person.id
    # Read back through the binding lookup: the row itself is not readable
    # by anyone but its owner, and nothing is armed here.
    bound = await person_by_subject("auth0|new")
    assert bound is not None and bound.person_id == person.id


async def test_binding_refuses_a_person_who_already_signed_in(tx, graph):
    """A bound row is not re-bindable, so a second provider cannot take it over."""
    person = await graph.person("taken@example.test", "Taken", subject="auth0|first")

    assert await bind_subject("taken@example.test", "auth0|second") is None
    unchanged = await person_by_subject("auth0|first")
    assert unchanged is not None and unchanged.person_id == person.id


async def test_binding_an_uninvited_address_matches_nothing(tx, graph):
    """Invitation-only: no row means no account, never an implicit signup."""
    assert await bind_subject("stranger@example.test", "auth0|stranger") is None
    rows = await Person.raw(
        "SELECT rif_person_id_by_email({}) AS id", "stranger@example.test"
    )
    assert rows[0]["id"] is None


async def test_two_concurrent_first_sign_ins_cannot_both_bind(graph):
    """The race the atomic bind exists to close.

    Two connections attempt to claim the same invitation at once. As a
    lookup followed by a write, both could pass the "is it unbound?" check
    before either wrote. As one UPDATE ... WHERE subject IS NULL RETURNING,
    the loser matches no row.

    Runs outside the ``tx`` fixture: the two attempts need to be genuinely
    concurrent on separate connections, which a single shared transaction
    cannot express.
    """
    await graph.person("race@example.test", "Race")

    async def attempt(subject: str) -> bool:
        async with DB.transaction():
            return await bind_subject("race@example.test", subject) is not None

    first, second = await asyncio.gather(attempt("auth0|a"), attempt("auth0|b"))

    assert [first, second].count(True) == 1, "exactly one attempt should bind"
    winners = [s for s in ("auth0|a", "auth0|b") if await person_by_subject(s)]
    assert len(winners) == 1


async def test_person_exists_reports_liveness_only(graph):
    """The cookie check needs a boolean, so a boolean is all that crosses."""
    person = await graph.person("alive@example.test", "Alive")
    assert await person_exists(person.id) is True

    await graph.erase_person(person)
    assert await person_exists(person.id) is False
