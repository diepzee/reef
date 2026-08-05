from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rif.config import get_settings
from rif.models import Base, Membership, Person, Space, SpaceKind
from rif.rls import enable_statements


@pytest_asyncio.fixture(scope="session")
async def engine():
    """Create the test schema once per session, including RLS policies.

    The policy DDL comes from ``rif.rls``, the same module
    ``migrations/versions/0f1d29c16349_initial_schema.py`` uses to build the
    real schema, so production and tests can never apply different
    policies.
    """
    engine = create_async_engine(get_settings().test_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for statement in enable_statements():
            await conn.execute(text(statement))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    """Yield a session in a transaction that always rolls back."""
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    async with factory() as session:
        yield session
    await transaction.rollback()
    await connection.close()


@pytest_asyncio.fixture
async def household(session) -> dict:
    """Two people, two personal spaces, one household space, four memberships.

    :returns: mapping with keys ``wouter``, ``partner``, ``w_personal``,
        ``p_personal``, ``shared``
    """
    wouter = Person(email="wouter@example.test", display_name="Wouter")
    partner = Person(email="partner@example.test", display_name="Partner")
    session.add_all([wouter, partner])
    await session.flush()
    w_personal = Space(slug="wouter", kind=SpaceKind.PERSONAL, owner_person_id=wouter.id)
    p_personal = Space(slug="partner", kind=SpaceKind.PERSONAL, owner_person_id=partner.id)
    shared = Space(slug="school", kind=SpaceKind.HOUSEHOLD)
    session.add_all([w_personal, p_personal, shared])
    await session.flush()
    session.add_all([
        Membership(person_id=wouter.id, space_id=w_personal.id),
        Membership(person_id=partner.id, space_id=p_personal.id),
        Membership(person_id=wouter.id, space_id=shared.id),
        Membership(person_id=partner.id, space_id=shared.id),
    ])
    await session.flush()
    return {"wouter": wouter, "partner": partner, "w_personal": w_personal,
            "p_personal": p_personal, "shared": shared}
