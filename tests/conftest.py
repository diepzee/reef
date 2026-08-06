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


@pytest_asyncio.fixture
async def household() -> dict:
    """Two people, two personal spaces, one household space, four memberships.

    :returns: mapping with keys ``wouter``, ``partner``, ``w_personal``,
        ``p_personal``, ``shared``
    """
    wouter = Person(email="wouter@example.test", display_name="Wouter")
    partner = Person(email="partner@example.test", display_name="Partner")
    await wouter.save()
    await partner.save()
    w_personal = Space(
        slug="wouter", kind=SpaceKind.PERSONAL.value, owner_person_id=wouter.id
    )
    p_personal = Space(
        slug="partner", kind=SpaceKind.PERSONAL.value, owner_person_id=partner.id
    )
    shared = Space(slug="school", kind=SpaceKind.HOUSEHOLD.value)
    for space in (w_personal, p_personal, shared):
        await space.save()
    await Membership.insert(
        Membership(person_id=wouter.id, space_id=w_personal.id),
        Membership(person_id=partner.id, space_id=p_personal.id),
        Membership(person_id=wouter.id, space_id=shared.id),
        Membership(person_id=partner.id, space_id=shared.id),
    )
    return {
        "wouter": wouter,
        "partner": partner,
        "w_personal": w_personal,
        "p_personal": p_personal,
        "shared": shared,
    }
