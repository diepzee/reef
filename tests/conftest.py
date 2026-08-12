"""Test fixtures: a real Postgres, real RLS policies, a real household.

``DATABASE_URL`` is set before any ``rif`` import because ``rif.db`` builds
its engine at module scope; pointing it at ``rif_test`` afterwards would be
too late.
"""

import os

from rif.config import get_settings

os.environ["DATABASE_URL"] = get_settings().test_database_url

import asyncpg
import httpx
import pytest_asyncio
from piccolo.table import create_db_tables, drop_db_tables

from rif.db import DB, transaction_scope
from rif.models import TABLES, Membership, Person, Space, SpaceKind
from rif.rls import AUTHZ_ROLE, constraint_statements, enable_statements

CONTENT_TABLES = ("revisions", "attachments", "promotions", "pages")

PROBE_ROLE = "rif_probe"
"""A non-owner login role standing in for production's ``rif_app``.

The suite's own connection is ``rif``, which *owns* the tables. An owner's
privileges are implicit, so column-level grants -- ``REVOKE UPDATE`` then
``GRANT UPDATE (version)`` -- do not constrain it the way they constrain
``rif_app``. Any test asserting "a member cannot rewrite a cove's slug" would
pass against the owner for the wrong reason. Tests that assert a *privilege*
rather than a *policy* must therefore run through :func:`probe`.
"""

_MISSING_PROBE_ROLE = f"""
The {PROBE_ROLE} role does not exist in the test cluster.

It is created at cluster bootstrap by docker/initdb, which only runs on a
fresh volume. On a cluster that predates it, create it once as the superuser:

    PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d postgres \\
      -c "CREATE ROLE {PROBE_ROLE} WITH LOGIN PASSWORD 'probe' NOSUPERUSER \\
          NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
    PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d rif_test \\
      -c "GRANT CONNECT ON DATABASE rif_test TO {PROBE_ROLE}" \\
      -c "GRANT USAGE ON SCHEMA public TO {PROBE_ROLE}"
"""


def probe_dsn() -> str:
    """Return the test database DSN with the probe role's credentials.

    :returns: a DSN usable by asyncpg
    """
    return _swap_user(get_settings().test_database_url, PROBE_ROLE, "probe")


def _swap_user(dsn: str, user: str, password: str) -> str:
    """Return ``dsn`` with its userinfo replaced.

    :param dsn: a ``postgresql://user:pass@host/db`` connection string
    :param user: replacement username
    :param password: replacement password
    :returns: the rewritten DSN
    """
    scheme, _, rest = dsn.partition("://")
    _, _, hostpart = rest.rpartition("@")
    return f"{scheme}://{user}:{password}@{hostpart}"


def seed_dsn() -> str:
    """Return a DSN able to insert fixture rows past the identity policies.

    Fixture data stands for rows that already exist -- people who were
    invited long ago, coves already created. Under the identity policies
    those rows cannot be *created* by an unarmed connection, and rightly so:
    ``persons_invite_insert`` demands an inviter, so a person with no inviter
    can only be seeded out of band. That is exactly how reef's own first
    person came to exist, and it stays a deliberate manual act rather than
    something the application can do.

    Defaults to the superuser the local ``docker-compose.yml`` creates.
    Override with ``RIF_SEED_DATABASE_URL`` for a cluster set up differently.

    :returns: a DSN usable by asyncpg
    """
    override = os.environ.get("RIF_SEED_DATABASE_URL")
    if override:
        return override
    return _swap_user(get_settings().test_database_url, "postgres", "postgres")


_MISSING_AUTHZ_ROLE = f"""
The {AUTHZ_ROLE} role does not exist in the test cluster, so the RLS helper
functions cannot be created and these tests would prove a shape production
does not have.

It is created at cluster bootstrap by docker/initdb, which only runs on a
fresh volume. On a cluster that predates it, create it once as the superuser:

    PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d postgres \\
      -c "CREATE ROLE {AUTHZ_ROLE} NOLOGIN BYPASSRLS" \\
      -c "GRANT {AUTHZ_ROLE} TO rif"
    PGPASSWORD=postgres psql -h localhost -p 5433 -U postgres -d rif_test \\
      -c "GRANT CREATE ON SCHEMA public TO {AUTHZ_ROLE}"
"""


@pytest_asyncio.fixture(scope="session", autouse=True)
async def schema():
    """Build the schema once per session, including RLS policies.

    The policy DDL comes from ``rif.rls``, the same module the real
    migration uses, so production and tests can never apply different
    policies -- a difference there would mean tests validate policies
    production does not enforce.

    That guarantee now extends to role shape, not just statements: the
    helper functions are owned by a ``BYPASSRLS`` role, and whether a policy
    recurses depends entirely on who owns them. A cluster missing the role
    fails here, loudly, rather than silently testing something else.
    """
    await DB.start_connection_pool()
    if not await DB._run_in_new_connection(
        f"SELECT 1 FROM pg_roles WHERE rolname = '{AUTHZ_ROLE}'"
    ):
        raise RuntimeError(_MISSING_AUTHZ_ROLE)
    if not await DB._run_in_new_connection(
        f"SELECT 1 FROM pg_roles WHERE rolname = '{PROBE_ROLE}'"
    ):
        raise RuntimeError(_MISSING_PROBE_ROLE)
    await drop_db_tables(*reversed(TABLES))
    await create_db_tables(*TABLES)
    for statement in constraint_statements() + enable_statements():
        await DB._run_in_new_connection(statement)
    # Granted here rather than in docker/initdb because the tables do not
    # exist at cluster bootstrap. Exactly what production grants rif_app --
    # no more, so a privilege the app does not have is one the probe does
    # not have either.
    for table in (*CONTENT_TABLES, "memberships", "spaces", "persons"):
        await DB._run_in_new_connection(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {PROBE_ROLE}"
        )
    yield
    await DB.close_connection_pool()


@pytest_asyncio.fixture
async def probe():
    """Yield a raw connection as the non-owner role, for privilege assertions.

    Use this instead of the ambient Piccolo connection whenever a test asserts
    that something is *refused*. The suite's own role owns the tables, so it
    is not bound by column grants and would report success where production
    reports "permission denied" -- see :data:`PROBE_ROLE`.

    Policies still apply to both roles (``FORCE ROW LEVEL SECURITY``), so
    row-visibility tests do not need this; privilege tests do.

    :returns: an open asyncpg connection, closed on teardown
    """
    connection = await asyncpg.connect(probe_dsn())
    try:
        yield connection
    finally:
        await connection.close()


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

    These seed *pre-existing* state, so they write through a connection that
    is not subject to the identity policies -- see :func:`seed_dsn` for why
    that is the honest shape rather than a shortcut. Behaviour under the
    policies is exercised by the code under test, never by these builders.

    Piccolo queries are ambient, so these are plain coroutines: the caller
    decides whether the code being tested runs inside a transaction.
    """

    def __init__(self, connection) -> None:
        """Hold the seeding connection.

        :param connection: an asyncpg connection able to bypass the policies
        """
        self._connection = connection

    async def person(
        self, email: str, display_name: str, invited_by: Person | None = None
    ) -> Person:
        """Create one person row.

        :param email: the person's email address
        :param display_name: how the person is addressed
        :param invited_by: the inviter, when the test cares about provenance
        :returns: the saved person
        """
        row = Person(email=email, display_name=display_name)
        if invited_by is not None:
            row.invited_by_person_id = invited_by.id
        await self._connection.execute(
            "INSERT INTO persons (id, email, display_name, invited_by_person_id) "
            "VALUES ($1, $2, $3, $4)",
            row.id,
            email,
            display_name,
            invited_by.id if invited_by is not None else None,
        )
        # The row was written behind Piccolo's back, so the model still
        # believes it is new and .save() would INSERT a duplicate rather than
        # UPDATE. Tests legitimately mutate seeded rows (binding a subject,
        # say), so tell the model what is true.
        row._exists_in_db = True
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
        await self._insert_space(space, owner)
        return space

    async def shared_space(self, slug: str, owner: Person, *members: Person) -> Space:
        """Create a shared space owned by ``owner``, with memberships.

        :param slug: the space's slug
        :param owner: the accountable owner, who is also a member
        :param members: further people to admit
        :returns: the saved space
        """
        space = Space(slug=slug, kind=SpaceKind.SHARED.value, owner_person_id=owner.id)
        await self._insert_space(space, owner, *members)
        return space

    async def _insert_space(self, space: Space, *members: Person) -> None:
        """Insert a space row and one membership per member.

        :param space: the space to write
        :param members: everyone who belongs to it
        """
        await self._connection.execute(
            "INSERT INTO spaces (id, slug, kind, owner_person_id, version) "
            "VALUES ($1, $2, $3, $4, 0)",
            space.id,
            space.slug,
            space.kind,
            space.owner_person_id,
        )
        space._exists_in_db = True
        for person in members:
            await self._connection.execute(
                "INSERT INTO memberships (person_id, space_id, role) "
                "VALUES ($1, $2, 'member')",
                person.id,
                space.id,
            )


@pytest_asyncio.fixture
async def graph():
    """Expose the topology builders to a test.

    :returns: the builder object
    """
    connection = await asyncpg.connect(seed_dsn())
    try:
        yield Graph(connection)
    finally:
        await connection.close()


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


@pytest_asyncio.fixture
async def api(monkeypatch, graph):
    """Stand up an HTTP client against the web JSON API with a fixed secret.

    Shared by ``test_web_api_read.py`` and ``test_web_api_write.py`` so both
    exercise the same registered routes and cookie-sealing secret.

    :param monkeypatch: pytest's monkeypatch fixture
    :param graph: the topology-builder fixture, pulled in for fixture ordering
    :returns: an async client bound to the FastMCP ASGI app
    """
    from rif.server import mcp
    from rif.web.routes_api import register_api_routes

    monkeypatch.setattr(get_settings(), "session_secret", "test-secret")
    register_api_routes(mcp)
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(
        transport=transport, base_url="https://rif.example"
    ) as client:
        yield client


def _login(client: httpx.AsyncClient, person) -> None:
    """Seal a session token for ``person`` and attach it to the client's cookies.

    :param client: the HTTP client to log in
    :param person: the person to seal a session for
    """
    from rif.web.session import seal

    token = seal(person.id, person.email, secret="test-secret")
    client.cookies.set("rif_session", token)


@pytest_asyncio.fixture
async def world(graph: Graph):
    """Two people; alice owns 'team' with bob.

    Builders run outside a transaction so the seeded rows are committed
    before any HTTP request runs -- the handlers under test open their own
    ``transaction_scope()`` and would not see uncommitted work.

    :param graph: the topology-builder fixture
    :returns: a tuple of ``(alice, bob, team)``
    """
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    await graph.personal_space(alice)
    await graph.personal_space(bob)
    team = await graph.shared_space("team", alice, bob)
    return alice, bob, team
