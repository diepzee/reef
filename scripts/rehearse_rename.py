"""Rehearse the rename of cove to space and page to engram, on local databases.

Builds two databases on the local cluster and takes both through the
migration ``2026-11-20T10:00:00`` and every migration after it:

- **chain**: every migration from an empty database, the way a fresh deploy
  or the restore drill builds one.
- **history**: the migrations as they stood at :data:`HISTORY_COMMIT`, the
  last commit before the August rename to cove, then today's. This is how
  production was built, and it gives indexes and constraints the names
  production has.

Before the rename, both are seeded with made-up rows. Afterwards the script
checks that:

- no row was lost;
- the two builds ended up with the same schema;
- no name in either still says cove or page;
- the helpers are exactly those :mod:`reef.rls` declares;
- row security still answers as it should, for every kind of person;
- the application's own code runs against the result.

It also checks that the history build has production's counts of objects
named cove or page, and that ``scripts/rename_sql.py`` still prints the
statements the migration holds. Then it drops both databases.

Local only. It refuses a database host that is not this machine, and refuses
when a ``RAILWAY_`` variable is set. It never reads production.

Run as ``python scripts/rehearse_rename.py [--keep]``. It needs ``git``, the
local cluster (``just db-up``) and its superuser. Set ``REEF_SEED_DATABASE_URL``
for a cluster set up differently. ``--keep`` leaves both databases in place.
"""

import asyncio
import importlib
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "src" / "reef" / "piccolo_migrations"

#: The rename migration this rehearses.
RENAME_ID = "2026-11-20T10:00:00:000000"

#: The last commit whose migrations still built ``spaces``, before the August
#: rename to cove. Production ran those, so its index and constraint names
#: come from them.
HISTORY_COMMIT = "da7ac39"

#: Objects named cove or page in production, measured on 19 November 2026.
PRODUCTION_COUNTS = {
    "tables": 6,
    "columns": 14,
    "functions": 5,
    "policies": 17,
    "indexes": 10,
}

DATABASES = {"chain": "reef_rehearse_chain", "history": "reef_rehearse_history"}

#: What ``tests/test_migration_chain.py`` provisions: what ``docker/initdb``
#: and ``scripts/provision_authz_role.sql`` give a real database.
PROVISION = (
    "ALTER SCHEMA public OWNER TO reef",
    "GRANT CREATE ON SCHEMA public TO reef_authz",
    "GRANT USAGE ON SCHEMA public TO reef_probe",
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    "CREATE EXTENSION IF NOT EXISTS vector",
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

W = "00000000-0000-0000-0000-00000000000a"
P = "00000000-0000-0000-0000-00000000000b"
V = "00000000-0000-0000-0000-00000000000c"
W_PERSONAL = "00000000-0000-0000-0000-0000000000c1"
HOUSEHOLD = "00000000-0000-0000-0000-0000000000c3"
HOUSE = "00000000-0000-0000-0000-0000000000a2"
BOILER = "00000000-0000-0000-0000-0000000000a3"

#: Made-up rows in the schema as it stands just before the rename, written
#: as the superuser, which row security does not bind. One of each kind of
#: row the rename touches: three people (one a viewer), two personal spaces
#: and a shared one, pages with history, a file, a link, usage, activation,
#: a logged search, a vector, a staged share, an appearance and a queued job.
SEED = f"""
INSERT INTO persons (id, email, display_name, subject, avatar_mime, avatar_bytes)
VALUES ('{W}', 'wouter@example.test', 'Wouter', 'sub-w', 'image/png', '\\x89504e47'),
       ('{P}', 'partner@example.test', 'Partner', 'sub-p', NULL, NULL),
       ('{V}', 'viewer@example.test', 'Viewer', NULL, NULL, NULL);
INSERT INTO coves (id, slug, kind, owner_person_id, version)
VALUES ('{W_PERSONAL}', 'wouter', 'personal', '{W}', 0),
       ('00000000-0000-0000-0000-0000000000c2', 'partner', 'personal', '{P}', 0),
       ('{HOUSEHOLD}', 'household', 'shared', '{W}', 0);
INSERT INTO memberships (person_id, cove_id, role, alias)
VALUES ('{W}', '{W_PERSONAL}', 'member', 'personal'),
       ('{P}', '00000000-0000-0000-0000-0000000000c2', 'member', 'personal'),
       ('{W}', '{HOUSEHOLD}', 'member', 'household'),
       ('{P}', '{HOUSEHOLD}', 'member', 'home'),
       ('{V}', '{HOUSEHOLD}', 'viewer', 'household');
INSERT INTO pages (id, cove_id, path, title, tags, body, version)
VALUES ('00000000-0000-0000-0000-0000000000a1', '{W_PERSONAL}', 'diary.md',
        'Diary', '{{}}', 'A private entry.', 1),
       ('{HOUSE}', '{HOUSEHOLD}', 'house.md', 'House', '{{core}}',
        'The boiler is a Vaillant.', 1),
       ('{BOILER}', '{HOUSEHOLD}', 'boiler.md', 'Boiler', '{{}}',
        'See [[house.md]].', 1),
       ('00000000-0000-0000-0000-0000000000a4',
        '00000000-0000-0000-0000-0000000000c2', 'notes.md', 'Notes', '{{}}',
        'Partner notes.', 1);
INSERT INTO revisions (id, page_id, path, title, tags, body, message, author_id)
SELECT gen_random_uuid(), id, path, title, tags, body, 'seed', '{W}' FROM pages
WHERE id::text LIKE '00000000-0000-0000-0000-0000000000a%';
INSERT INTO attachments (id, cove_id, page_id, object_key, filename, mime,
                         byte_size, description, status)
VALUES (gen_random_uuid(), '{HOUSEHOLD}', '{HOUSE}', 'attachments/rehearsal-1',
        'manual.pdf', 'application/pdf', 10, 'Boiler manual', 'ready');
INSERT INTO links (id, from_page_id, to_page_id, kind, source, status,
                   target_text, author_id)
VALUES (gen_random_uuid(), '{BOILER}', '{HOUSE}', 'references', 'wikilink',
        'active', 'house.md', '{W}');
INSERT INTO page_access (id, person_id, page_id, via, weight, session_id)
VALUES (gen_random_uuid(), '{W}', '{HOUSE}', 'read', 1.0, 's1');
INSERT INTO page_heat (id, person_id, page_id, activation, accesses, last_access)
VALUES (gen_random_uuid(), '{W}', '{HOUSE}', 0.5, 1, LOCALTIMESTAMP);
INSERT INTO search_log (id, person_id, query, page_ids, session_id)
VALUES (gen_random_uuid(), '{W}', 'boiler', '{{{HOUSE}}}', 's1');
INSERT INTO page_embeddings (id, page_id, section_anchor, model_id, dims,
                             content_hash, embedding)
VALUES (gen_random_uuid(), '{HOUSE}', NULL, 'rehearsal', 3, 'h1', '[0.1,0.2,0.3]');
INSERT INTO embedding_spend (id, person_id, day, pieces)
VALUES (gen_random_uuid(), '{W}', CURRENT_DATE, 1);
INSERT INTO promotions (id, person_id, source_page_id, source_version,
                        dest_cove_id, dest_path, created_at)
VALUES (gen_random_uuid(), '{W}', '00000000-0000-0000-0000-0000000000a1', 1,
        '{HOUSEHOLD}', 'diary.md', LOCALTIMESTAMP);
INSERT INTO cove_appearances (id, person_id, cove_id, color, glyph)
VALUES (gen_random_uuid(), '{W}', '{HOUSEHOLD}', 'violet', 'spiral');
INSERT INTO jobs (id, person_id, kind, subject_ids, status)
VALUES (gen_random_uuid(), '{W}', 'embed_page', '{{{HOUSE}}}', 'queued');
"""

OLD_WORDS = "(cove|page)"

#: Objects whose names hold the old words, counted the way production was.
COUNTS = {
    "tables": f"SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
    f"AND tablename ~ '{OLD_WORDS}'",
    "columns": f"SELECT count(*) FROM information_schema.columns "
    f"WHERE table_schema = 'public' AND column_name ~ '{OLD_WORDS}'",
    "functions": f"SELECT count(*) FROM pg_proc "
    f"WHERE pronamespace = 'public'::regnamespace AND proname ~ '{OLD_WORDS}'",
    "policies": f"SELECT count(*) FROM pg_policies WHERE schemaname = 'public' "
    f"AND policyname ~ '{OLD_WORDS}'",
    "indexes": f"SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' "
    f"AND indexname ~ '{OLD_WORDS}'",
}

#: Everything the two builds must agree on. Index columns keep the name they
#: were created with and are invisible to SQL, so they are left out.
#:
#: Two differences in the history that the rename does not touch are masked.
#: ``promotions.created_at`` defaults to the moment the first migration ran,
#: written into the schema as a literal. And the test suite's ``reef_probe``,
#: which production does not have, was granted ``space_appearances`` under
#: its old name, ``rif_probe``, so only the chain build grants it there.
SHAPE = {
    "columns": "SELECT table_name, column_name, data_type, is_nullable, "
    "regexp_replace(column_default, '^''[0-9][^'']*''::timestamp', 'when-migrated') "
    "FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name <> 'migration'",
    "policies": "SELECT tablename, policyname, permissive, roles::text, cmd, qual, "
    "with_check FROM pg_policies WHERE schemaname = 'public'",
    "functions": "SELECT proname, pg_get_function_arguments(oid), "
    "pg_get_function_result(oid), prosrc, pg_get_userbyid(proowner), "
    "proacl::text FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
    "AND proname LIKE 'reef%'",
    "row_security": "SELECT relname, relrowsecurity, relforcerowsecurity "
    "FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'",
    "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes "
    "WHERE schemaname = 'public'",
    "constraints": "SELECT conrelid::regclass::text, conname, "
    "pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE connamespace = 'public'::regnamespace",
    "grants": "SELECT table_name, column_name, privilege_type, grantee "
    "FROM information_schema.column_privileges WHERE table_schema = 'public' "
    "AND grantee <> 'reef_probe'",
}

#: Every name that would still say cove or page, with what kind of object it is.
OLD_NAMES = f"""
SELECT 'table ' || relname FROM pg_class
WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'i', 'S')
  AND relname ~ '{OLD_WORDS}'
UNION ALL
SELECT 'column ' || attrelid::regclass || '.' || attname FROM pg_attribute
JOIN pg_class ON pg_class.oid = attrelid
WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' AND attnum > 0
  AND attname ~ '{OLD_WORDS}'
UNION ALL
SELECT 'function ' || oid::regprocedure FROM pg_proc
WHERE pronamespace = 'public'::regnamespace
  AND (proname ~ '{OLD_WORDS}' OR pg_get_function_arguments(oid) ~ 'p_{OLD_WORDS}'
       OR prosrc ~ '{OLD_WORDS}')
UNION ALL
SELECT 'policy ' || polname FROM pg_policy
WHERE polname ~ '{OLD_WORDS}'
   OR coalesce(pg_get_expr(polqual, polrelid), '')
      || coalesce(pg_get_expr(polwithcheck, polrelid), '') ~ '{OLD_WORDS}'
UNION ALL
SELECT 'constraint ' || conname FROM pg_constraint
WHERE connamespace = 'public'::regnamespace AND conname ~ '{OLD_WORDS}'
"""


class NotLocal(Exception):
    """Raised when the rehearsal would touch a database not on this machine."""


class Report:
    """Collects the outcome of every check and prints it as it goes."""

    def __init__(self) -> None:
        """Start with no failures."""
        self.failures: list[str] = []

    def check(self, label: str, ok: bool, detail: object = "") -> None:
        """Record one check.

        :param label: what was checked, as a sentence
        :param ok: whether it held
        :param detail: what to show when it did not
        """
        print(f"{'ok  ' if ok else 'FAIL'}  {label}")
        if not ok:
            print(f"      {detail}")
            self.failures.append(label)


def superuser_dsn(database: str) -> str:
    """Return the local superuser's DSN for ``database``.

    :param database: the database to connect to
    :returns: a DSN usable by asyncpg
    :raises NotLocal: if the cluster is not on this machine
    """
    override = os.environ.get("REEF_SEED_DATABASE_URL")
    base = urlsplit(
        override or "postgresql://postgres:postgres@localhost:5433/postgres"
    )
    if any(name.startswith("RAILWAY_") for name in os.environ):
        raise NotLocal("a RAILWAY_ variable is set; this runs locally only")
    if base.hostname not in _LOCAL_HOSTS:
        raise NotLocal(f"the host {base.hostname!r} is not this machine")
    return urlunsplit(base._replace(path=f"/{database}"))


def owner_dsn(database: str) -> str:
    """Return the DSN of ``reef``, the role that owns the databases locally.

    Migrations run as it, as they do in the drill, so the rehearsal has the
    privileges the chain actually has.

    :param database: the database to connect to
    :returns: a DSN usable by asyncpg
    """
    base = urlsplit(superuser_dsn(database))
    netloc = f"reef:reef@{base.hostname}:{base.port}"
    return urlunsplit(base._replace(netloc=netloc))


def migration_before(migration_id: str) -> str:
    """Return the ID of the migration that runs just before ``migration_id``.

    :param migration_id: a migration in this repository
    :returns: the ID of its predecessor
    """
    ids = sorted(
        match[1]
        for path in MIGRATIONS.glob("reef_*.py")
        if (match := re.search(r'^ID = "([^"]+)"', path.read_text(), re.MULTILINE))
    )
    return ids[ids.index(migration_id) - 1]


async def create(database: str) -> None:
    """Create ``database`` empty, dropping it first, and provision it.

    :param database: the name to create
    """
    await drop(database)
    admin = await asyncpg.connect(superuser_dsn("postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{database}" OWNER reef')
    finally:
        await admin.close()
    provisioner = await asyncpg.connect(superuser_dsn(database))
    try:
        for statement in PROVISION:
            await provisioner.execute(statement)
    finally:
        await provisioner.close()


async def drop(database: str) -> None:
    """Drop ``database`` if it exists.

    :param database: the name to drop
    """
    admin = await asyncpg.connect(superuser_dsn("postgres"))
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        await admin.close()


def migrate(database: str, code: Path, upto: str = "all") -> None:
    """Run the migrations of the checkout at ``code`` against ``database``.

    :param database: the database to migrate
    :param code: a directory holding ``piccolo_conf.py`` and ``src/reef``
    :param upto: the last migration to run, by ID; every one by default
    :raises RuntimeError: if a migration fails
    """
    result = subprocess.run(
        [sys.executable, "-m", "piccolo.main", "migrations", "forwards", "reef", upto],
        cwd=code,
        env={
            **os.environ,
            "DATABASE_URL": owner_dsn(database),
            "PYTHONPATH": str(code / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    # Piccolo exits 0 even when a migration fails; see test_migration_chain.
    if result.returncode != 0 or "The command failed" in output:
        raise RuntimeError(f"migrating {database} failed:\n{output}")


def history_checkout(target: Path) -> Path:
    """Extract the migrations as they stood at :data:`HISTORY_COMMIT`.

    :param target: an empty directory to extract into
    :returns: ``target``
    """
    archive = subprocess.run(
        ["git", "archive", "--format=tar", HISTORY_COMMIT, "src", "piccolo_conf.py"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")
    return target


async def fetch_all(database: str, queries: dict[str, str]) -> dict[str, set]:
    """Run each query as the superuser and collect its rows.

    :param database: the database to read
    :param queries: SQL by name
    :returns: the rows of each query, as a set of tuples, by name
    """
    connection = await asyncpg.connect(superuser_dsn(database))
    try:
        return {
            name: {tuple(row) for row in await connection.fetch(sql)}
            for name, sql in queries.items()
        }
    finally:
        await connection.close()


async def row_counts(database: str) -> dict[str, int]:
    """Count the rows of every table, keyed by the table's name after the rename.

    :param database: the database to count
    :returns: rows per table
    """
    connection = await asyncpg.connect(superuser_dsn(database))
    try:
        tables = await connection.fetch(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename <> 'migration'"
        )
        return {
            renamed(row["tablename"]): await connection.fetchval(
                f'SELECT count(*) FROM "{row["tablename"]}"'
            )
            for row in tables
        }
    finally:
        await connection.close()


def renamed(name: str) -> str:
    """Return ``name`` with cove spelled space and page spelled engram.

    :param name: a table name
    :returns: the name after the rename
    """
    return name.replace("cove", "space").replace("page", "engram")


async def seed(database: str) -> None:
    """Write the made-up rows of :data:`SEED`, in one transaction.

    :param database: the database to seed
    """
    connection = await asyncpg.connect(superuser_dsn(database))
    try:
        async with connection.transaction():
            await connection.execute(SEED)
    finally:
        await connection.close()


async def check_generator(report: Report) -> None:
    """Check that rename_sql.py still prints what the migration holds.

    :param report: where to record the outcome
    """
    # Beside this script, so already on the path.
    import rename_sql

    migration = importlib.import_module(
        "reef.piccolo_migrations.reef_2026_11_20t10_00_00_000000"
    )
    catalogs = [
        await rename_sql.read_catalog(owner_dsn(name)) for name in DATABASES.values()
    ]
    union = {
        section: set().union(*(catalog[section] for catalog in catalogs))
        for section in catalogs[0]
    }
    printed = {
        block.split("\n", 1)[0].split(":")[0].removeprefix("-- "): (
            block.split("\n", 1)[1] if "\n" in block else ""
        ).strip()
        for block in rename_sql.render(union).strip().split("\n\n")
    }
    for name, text in printed.items():
        report.check(
            f"the migration's {name} is what rename_sql.py prints",
            getattr(migration, name).strip() == text,
            name,
        )


async def as_person(connection, person: str | None, sql: str, *args) -> list:
    """Run one statement armed as ``person``, then roll it back.

    :param connection: a connection as the owning role, which row security
        binds because every table forces it
    :param person: whose id to arm, or ``None`` for nobody
    :param sql: the statement
    :param args: its arguments
    :returns: the rows it returned
    """
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute(
            "SELECT set_config('app.person_id', $1, true)", person or ""
        )
        return await connection.fetch(sql, *args)
    finally:
        await transaction.rollback()


async def refused(connection, person: str, sql: str, *args) -> bool:
    """Report whether a write armed as ``person`` is refused, and roll it back.

    :param connection: a connection as the owning role
    :param person: whose id to arm
    :param sql: the write
    :param args: its arguments
    :returns: True if row security refused or filtered out the write
    """
    try:
        rows = await as_person(connection, person, sql + " RETURNING 1", *args)
    except asyncpg.InsufficientPrivilegeError:
        return True
    return rows == []


async def check_row_security(database: str, report: Report) -> None:
    """Check row security on the seeded rows, armed as each kind of person.

    :param database: the migrated database
    :param report: where to record the outcome
    """
    c = await asyncpg.connect(owner_dsn(database))
    insert = (
        "INSERT INTO engrams (id, space_id, path, title, tags, body, version, "
        "created_at, updated_at) VALUES (gen_random_uuid(), $1, 'new.md', 'x', "
        "'{}', 'x', 1, now(), now())"
    )

    async def paths(person: str | None) -> list[str]:
        rows = await as_person(c, person, "SELECT path FROM engrams")
        return sorted(row["path"] for row in rows if row["path"].endswith(".md"))

    async def count(person: str, sql: str, *args) -> int:
        return len(await as_person(c, person, sql, *args))

    try:
        seeded = {"boiler.md", "diary.md", "house.md", "notes.md"}
        report.check(
            "each person reads their own engrams and the shared ones",
            [set(await paths(p)) & seeded for p in (W, P, V)]
            == [
                {"boiler.md", "diary.md", "house.md"},
                {"boiler.md", "house.md", "notes.md"},
                {"boiler.md", "house.md"},
            ],
        )
        report.check("nobody armed reads nothing", await paths(None) == [])
        report.check("a viewer cannot write", await refused(c, V, insert, HOUSEHOLD))
        report.check(
            "a viewer cannot change",
            await refused(
                c, V, "UPDATE engrams SET body = 'x' WHERE space_id = $1", HOUSEHOLD
            ),
        )
        report.check(
            "nobody writes into another person's personal space",
            await refused(c, P, insert, W_PERSONAL),
        )
        report.check(
            "a member can write",
            len(await as_person(c, P, insert + " RETURNING 1", HOUSEHOLD)) == 1,
        )
        roster = await as_person(c, W, "SELECT * FROM reef_roster($1)", HOUSEHOLD)
        report.check(
            "the owner's roster carries addresses",
            sorted(r["member_email"] for r in roster)
            == ["partner@example.test", "viewer@example.test", "wouter@example.test"],
        )
        roster = await as_person(c, P, "SELECT * FROM reef_roster($1)", HOUSEHOLD)
        report.check(
            "a member's roster carries none",
            {r["member_email"] for r in roster} == {""},
        )
        owner = await as_person(c, P, "SELECT * FROM reef_space_owner($1)", HOUSEHOLD)
        report.check(
            "reef_space_owner answers a member",
            [r["owner_name"] for r in owner] == ["Wouter"],
        )
        faces = await as_person(c, P, "SELECT * FROM reef_member_faces($1)", HOUSEHOLD)
        report.check(
            "reef_member_faces answers", [r["avatar_len"] for r in faces] == [4]
        )
        report.check(
            "reef_member_avatar answers",
            await count(P, "SELECT * FROM reef_member_avatar($1, $2)", HOUSEHOLD, W)
            == 1,
        )
        report.check(
            "reef_space_ids and reef_member_space_ids answer",
            (
                await count(W, "SELECT reef_space_ids()"),
                await count(V, "SELECT reef_member_space_ids()"),
            )
            == (2, 0),
        )
        report.check(
            "links need both ends readable",
            (
                await count(P, "SELECT 1 FROM links"),
                await count(V, "SELECT 1 FROM links"),
            )
            == (1, 1),
        )
        for table in (
            "engram_access",
            "engram_heat",
            "search_log",
            "promotions",
            "space_appearances",
            "jobs",
            "embedding_spend",
        ):
            report.check(
                f"{table} is private to its person",
                (
                    await count(W, f"SELECT 1 FROM {table}"),
                    await count(P, f"SELECT 1 FROM {table}"),
                )
                == (1, 0),
            )
        report.check(
            "a viewer reads vectors but cannot plant one",
            await count(V, "SELECT 1 FROM engram_embeddings") == 1
            and await refused(
                c,
                V,
                "INSERT INTO engram_embeddings (id, engram_id, model_id, dims, "
                "content_hash, embedding, created_at) VALUES (gen_random_uuid(), "
                "$1, 'm', 3, 'h', '[1,2,3]', now())",
                HOUSE,
            ),
        )
        try:
            for sql, args in (
                ("SELECT reef_retire_links($1)", (BOILER,)),
                ("SELECT * FROM reef_remove_member($1, $2)", (HOUSEHOLD, V)),
                ("SELECT reef_transfer_space_ownership($1, $2)", (HOUSEHOLD, P)),
            ):
                await as_person(c, W, sql, *args)
            report.check("the writing helpers run", True)
        except asyncpg.PostgresError as error:
            report.check("the writing helpers run", False, repr(error))
    finally:
        await c.close()


async def check_schema(report: Report) -> None:
    """Check names, helpers and agreement between the two builds.

    :param report: where to record the outcome
    """
    from reef import rls

    statements = " ".join(
        rls.enable_statements()
        + rls.appearance_statements()
        + rls.job_statements()
        + rls.link_statements()
        + rls.usage_statements()
        + rls.search_log_statements()
        + rls.embedding_statements()
        + rls.session_epoch_statements()
        + rls.alias_statements()
        + rls.open_door_statements()
        + rls.avatar_statements()
    )
    declared = set(re.findall(r"CREATE OR REPLACE FUNCTION (reef_\w+)\(", statements))
    shapes = {}
    for build, database in DATABASES.items():
        shapes[build] = await fetch_all(database, SHAPE)
        old = await fetch_all(database, {"old": OLD_NAMES})
        report.check(
            f"{build}: no name says cove or page",
            not old["old"],
            sorted(old["old"])[:10],
        )
        helpers = {row[0] for row in shapes[build]["functions"]}
        report.check(
            f"{build}: the helpers are exactly those reef.rls declares",
            helpers == declared,
            (sorted(helpers - declared), sorted(declared - helpers)),
        )
        owners = {row[4] for row in shapes[build]["functions"]}
        report.check(
            f"{build}: every helper is owned by reef_authz", owners == {"reef_authz"}
        )
    for part in SHAPE:
        chain, history = shapes["chain"][part], shapes["history"][part]
        report.check(
            f"both builds have the same {part}",
            chain == history,
            (sorted(chain - history)[:4], sorted(history - chain)[:4]),
        )


def check_application(database: str, report: Report) -> None:
    """Run the application's own code against ``database``, in a child process.

    A child, because :mod:`reef.db` binds its engine to ``DATABASE_URL`` when
    it is first imported.

    :param database: the migrated database
    :param report: where to record the outcome
    """
    result = subprocess.run(
        [sys.executable, __file__, "--application"],
        env={
            **os.environ,
            "DATABASE_URL": owner_dsn(database),
            "PYTHONPATH": str(ROOT / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    report.check(
        "the application's code runs against it",
        result.returncode == 0,
        (result.stdout + result.stderr)[-2000:],
    )


async def application() -> None:
    """Read and write through reef's own modules, as two people.

    Every write is rolled back. Runs in the child :func:`check_application`
    starts.

    :raises AssertionError: if something reads back wrong
    """
    from uuid import UUID

    from reef.access import Principal, accessible_spaces, resolve_space
    from reef.context import build_index, load_context
    from reef.db import DB, transaction_scope
    from reef.engrams import get_engram, list_engrams, save_engram
    from reef.search import search_engrams
    from reef.spaces import member_roster

    class Rollback(Exception):
        """Ends the writing transaction without committing it."""

    await DB.start_connection_pool()
    wouter = Principal(UUID(W), "wouter@example.test")
    partner = Principal(UUID(P), "partner@example.test")
    try:
        async with transaction_scope():
            index = await build_index(wouter)
            assert sorted(entry.alias for entry in index.coves) == [
                "household",
                "personal",
            ]
            house = next(entry for entry in index.coves if entry.alias == "household")
            assert sorted(e["path"] for e in house.pages) == ["boiler.md", "house.md"]
            assert (
                next(e for e in house.pages if e["path"] == "house.md")["inbound"] == 1
            )
            spaces = sorted(space.slug for space in await accessible_spaces(partner))
            assert spaces == ["household", "partner"]
            house_md = await get_engram(partner, "home", "house.md")
            assert house_md.body == "The boiler is a Vaillant."
            assert await get_engram(partner, "personal", "diary.md") is None
            assert [e.path for e in await list_engrams(wouter, "personal")] == [
                "diary.md"
            ]
            hits = await search_engrams(wouter, "boiler", log=False)
            assert {hit.get("path") for hit in hits} >= {"house.md", "boiler.md"}
            household = await resolve_space(wouter, "household")
            roster = await member_roster(household.id)
            assert {member["avatar_len"] for member in roster} == {None, 4}
            assert (await load_context(wouter, char_budget=100_000)).page_count == 3
        try:
            async with transaction_scope():
                saved = await save_engram(
                    partner, "home", "new.md", "After the rename.", message="x"
                )
                assert saved.version == 1
                raise Rollback
        except Rollback:
            pass
    finally:
        await DB.close_connection_pool()


async def rehearse(keep: bool) -> int:
    """Build, seed, migrate and check both databases.

    :param keep: leave the databases in place afterwards
    :returns: the process exit code
    """
    report = Report()
    before_rename = migration_before(RENAME_ID)
    chain, history = DATABASES["chain"], DATABASES["history"]
    print(f"building {chain} from empty, up to {before_rename}")
    await create(chain)
    migrate(chain, ROOT, before_rename)
    print(f"building {history} at {HISTORY_COMMIT}, then up to {before_rename}")
    await create(history)
    with tempfile.TemporaryDirectory() as old:
        migrate(history, history_checkout(Path(old)))
    migrate(history, ROOT, before_rename)

    counts = await fetch_all(history, COUNTS)
    measured = {name: next(iter(rows))[0] for name, rows in counts.items()}
    report.check(
        "history: objects named cove or page match production's",
        measured == PRODUCTION_COUNTS,
        measured,
    )
    helpers = await fetch_all(
        history,
        {
            "avatar": "SELECT oid::regprocedure::text FROM pg_proc "
            "WHERE proname IN ('reef_member_faces', 'reef_member_avatar')"
        },
    )
    report.check(
        "history: both avatar helpers exist before the rename, as in production",
        {row[0] for row in helpers["avatar"]}
        == {"reef_member_faces(uuid)", "reef_member_avatar(uuid,uuid)"},
        helpers["avatar"],
    )
    await check_generator(report)

    rows = {}
    for database in DATABASES.values():
        await seed(database)
        rows[database] = await row_counts(database)
    print(f"migrating both through {RENAME_ID} and every later migration")
    for database in DATABASES.values():
        migrate(database, ROOT)

    for build, database in DATABASES.items():
        after = await row_counts(database)
        report.check(
            f"{build}: every table kept its rows",
            {name: after.get(name) for name in rows[database]} == rows[database],
            (rows[database], after),
        )
    await check_schema(report)
    for build, database in DATABASES.items():
        print(f"{build}:")
        await check_row_security(database, report)
        check_application(database, report)

    if not keep:
        for database in DATABASES.values():
            await drop(database)
    print(f"{len(report.failures)} failed" if report.failures else "all checks passed")
    return 1 if report.failures else 0


if __name__ == "__main__":
    if "--application" in sys.argv:
        asyncio.run(application())
    else:
        raise SystemExit(asyncio.run(rehearse(keep="--keep" in sys.argv)))
