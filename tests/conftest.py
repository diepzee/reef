"""Test fixtures: a real Postgres, real RLS policies, a real household.

``DATABASE_URL`` is set before any ``rif`` import because ``rif.db`` builds
its engine at module scope; pointing it at ``rif_test`` afterwards would be
too late.
"""

import os

from rif.config import get_settings

os.environ["DATABASE_URL"] = get_settings().test_database_url

import pytest_asyncio
from piccolo.table import create_db_tables, drop_db_tables

from rif.db import DB, transaction_scope
from rif.models import TABLES, Membership, Person, Space, SpaceKind
from rif.rls import constraint_statements, enable_statements

CONTENT_TABLES = ("revisions", "attachments", "promotions", "pages")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def schema():
    """Build the schema once per session, including RLS policies.

    The policy DDL comes from ``rif.rls``, the same module the real
    migration uses, so production and tests can never apply different
    policies -- a difference there would mean tests validate policies
    production does not enforce.
    """
    await DB.start_connection_pool()
    await drop_db_tables(*reversed(TABLES))
    await create_db_tables(*TABLES)
    for statement in constraint_statements() + enable_statements():
        await DB._run_in_new_connection(statement)
    yield
    await DB.close_connection_pool()


@pytest_asyncio.fixture(autouse=True)
async def clean():
    """Empty every table between tests.

    TRUNCATE, not DELETE: an unarmed DELETE against an RLS-protected table
    is itself filtered and would silently remove nothing.
    """
    await DB._run_in_new_connection(
        f"TRUNCATE {', '.join(CONTENT_TABLES)}, memberships, spaces, persons "
        f"RESTART IDENTITY CASCADE"
    )


@pytest_asyncio.fixture
async def tx():
    """Run the test inside one transaction, so arming RLS sticks.

    The SQLAlchemy suite passed a ``session`` into every call; Piccolo
    queries are ambient, so what a test needs instead is simply to be inside
    a transaction. Tests that manage their own scopes (``test_security``)
    do not request this.
    """
    async with transaction_scope():
        yield


class Graph:
    """Builders for arbitrary person/space/membership topologies.

    Piccolo queries are ambient, so these are plain coroutines rather than
    session-bound ones: the caller decides whether they run inside a
    transaction. Persons, spaces, and memberships carry no RLS policy, so a
    builder never needs to be armed.
    """

    async def person(self, email: str, display_name: str) -> Person:
        """Create one person row.

        :param email: the person's email address
        :param display_name: how the person is addressed
        :returns: the saved person
        """
        row = Person(email=email, display_name=display_name)
        await row.save()
        return row

    async def personal_space(self, owner: Person, slug: str | None = None) -> Space:
        """Create a personal space plus its single membership.

        :param owner: the person the space belongs to
        :param slug: explicit slug; defaults to the onboarding form
        :returns: the saved space
        """
        space = Space(
            slug=slug or f"personal-{owner.id.hex}",
            kind=SpaceKind.PERSONAL.value,
            owner_person_id=owner.id,
        )
        await space.save()
        await Membership(person_id=owner.id, space_id=space.id).save()
        return space

    async def shared_space(self, slug: str, owner: Person, *members: Person) -> Space:
        """Create a shared space owned by ``owner``, with memberships.

        :param slug: the space's slug
        :param owner: the accountable owner, who is also a member
        :param members: further people to admit
        :returns: the saved space
        """
        space = Space(slug=slug, kind=SpaceKind.SHARED.value, owner_person_id=owner.id)
        await space.save()
        await Membership.insert(
            *[
                Membership(person_id=person.id, space_id=space.id)
                for person in (owner, *members)
            ]
        )
        return space


@pytest_asyncio.fixture
async def graph() -> Graph:
    """Expose the topology builders to a test.

    :returns: the builder object
    """
    return Graph()


@pytest_asyncio.fixture
async def household(graph: Graph) -> dict:
    """Two people, two personal spaces, one shared space they both belong to.

    :returns: mapping with keys ``wouter``, ``partner``, ``w_personal``,
        ``p_personal``, ``shared``
    """
    wouter = await graph.person("wouter@example.test", "Wouter")
    partner = await graph.person("partner@example.test", "Partner")
    w_personal = await graph.personal_space(wouter, slug="wouter")
    p_personal = await graph.personal_space(partner, slug="partner")
    shared = await graph.shared_space("household", wouter, partner)
    return {
        "wouter": wouter,
        "partner": partner,
        "w_personal": w_personal,
        "p_personal": p_personal,
        "shared": shared,
    }
