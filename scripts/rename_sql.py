"""Print the SQL that renames cove to space and page to engram in a database.

Reads the catalog of each database it is given and prints, section by
section, the statements in ``reef_2026_11_20t10_00_00_000000.py``:

- ``ALTER ... RENAME`` for every table, column, helper function, index and
  constraint whose name holds ``cove`` or ``page``;
- ``DROP POLICY`` for every policy, and ``DROP FUNCTION`` for every helper
  whose parameter names hold the old words, so the migration can apply
  :mod:`reef.rls` afresh afterwards.

Every name in the schema that holds one of the words means it in the domain
sense, so the rule is a plain substitution: ``cove`` becomes ``space`` and
``page`` becomes ``engram``, which turns ``coves`` into ``spaces`` and
``pages`` into ``engrams`` on the way.

Indexes and constraints are named after the table and column they were
created on, so their names depend on a database's history. Production's
still say ``space`` from before the August rename to cove; a database built
from scratch says ``cove``. Give the script every database whose names the
migration has to handle: it prints the union, and each statement for an
index or constraint skips a name the database does not have. Tables, columns
and functions must agree across the databases, and are renamed unguarded.

Run as ``python scripts/rename_sql.py [DSN ...]``. Without a DSN it reads the
local development database. It only reads.
"""

import asyncio
import re
import sys

import asyncpg

from reef.config import get_settings

OLD_WORDS = r"cove|page"
"""What the catalog queries match, as a Postgres regular expression."""

TABLES = f"""
SELECT relname AS table_name
FROM pg_class JOIN pg_namespace ON pg_namespace.oid = relnamespace
WHERE nspname = 'public' AND relkind = 'r' AND relname ~ '{OLD_WORDS}'
"""

COLUMNS = f"""
SELECT relname AS table_name, attname AS column_name
FROM pg_attribute
JOIN pg_class ON pg_class.oid = attrelid
JOIN pg_namespace ON pg_namespace.oid = relnamespace
WHERE nspname = 'public' AND relkind = 'r' AND attnum > 0
  AND NOT attisdropped AND attname ~ '{OLD_WORDS}'
"""

FUNCTIONS = f"""
SELECT proname AS name, oidvectortypes(proargtypes) AS arguments
FROM pg_proc JOIN pg_namespace ON pg_namespace.oid = pronamespace
WHERE nspname = 'public' AND proname ~ '{OLD_WORDS}'
"""

#: Helpers whose *parameters* are named after the old words, which by this
#: codebase's convention start with ``p_``. ``CREATE OR REPLACE FUNCTION``
#: cannot rename a parameter, so these are dropped and created again.
PARAMETERS = f"""
SELECT proname AS name, oidvectortypes(proargtypes) AS arguments
FROM pg_proc JOIN pg_namespace ON pg_namespace.oid = pronamespace
WHERE nspname = 'public' AND proname LIKE 'reef\\_%'
  AND pg_get_function_arguments(pg_proc.oid) ~ '\\mp_({OLD_WORDS})'
"""

INDEXES = f"""
SELECT indexname AS index_name FROM pg_indexes
WHERE schemaname = 'public' AND indexname ~ '{OLD_WORDS}'
"""

#: Constraints with no index of their own. A primary key, unique or
#: exclusion constraint shares its index's name, and renaming the index
#: renames the constraint with it.
CONSTRAINTS = f"""
SELECT relname AS table_name, conname AS constraint_name
FROM pg_constraint
JOIN pg_class ON pg_class.oid = conrelid
JOIN pg_namespace ON pg_namespace.oid = relnamespace
WHERE nspname = 'public' AND contype NOT IN ('p', 'u', 'x')
  AND conname ~ '{OLD_WORDS}'
"""

POLICIES = """
SELECT tablename AS table_name, policyname AS policy_name FROM pg_policies
WHERE schemaname = 'public'
"""

RENAME_CONSTRAINTS = """\
DO $constraints$
DECLARE
    moved record;
BEGIN
    FOR moved IN
        SELECT * FROM (VALUES
{rows}
        ) AS t(table_name, old_name, new_name)
    LOOP
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = format('public.%I', moved.table_name)::regclass
              AND conname = moved.old_name
        ) THEN
            EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I',
                           moved.table_name, moved.old_name, moved.new_name);
        END IF;
    END LOOP;
END
$constraints$;"""


def renamed(name: str) -> str:
    """Return ``name`` with cove spelled space and page spelled engram.

    :param name: a table, column, function, index or constraint name
    :returns: the name under the new words
    """
    return re.sub("page", "engram", re.sub("cove", "space", name))


async def read_catalog(dsn: str) -> dict[str, set[tuple[str, ...]]]:
    """Return the names in one database that the rename touches.

    :param dsn: the database to read
    :returns: one set of rows per catalog query, keyed by section
    """
    connection = await asyncpg.connect(dsn)
    try:
        queries = {
            "tables": TABLES,
            "columns": COLUMNS,
            "functions": FUNCTIONS,
            "parameters": PARAMETERS,
            "indexes": INDEXES,
            "constraints": CONSTRAINTS,
            "policies": POLICIES,
        }
        return {
            section: {tuple(row) for row in await connection.fetch(query)}
            for section, query in queries.items()
        }
    finally:
        await connection.close()


def render(catalog: dict[str, set[tuple[str, ...]]]) -> str:
    """Return the statements for a catalog, one section per migration constant.

    :param catalog: the union of :func:`read_catalog` over every database
    :returns: SQL, with a comment naming each section and its size
    """
    tables = [
        f"ALTER TABLE {table} RENAME TO {renamed(table)};"
        for (table,) in sorted(catalog["tables"])
    ]
    columns = [
        f"ALTER TABLE {renamed(table)} RENAME COLUMN {column} TO {renamed(column)};"
        for table, column in sorted(catalog["columns"])
    ]
    functions = [
        f"ALTER FUNCTION {name}({arguments}) RENAME TO {renamed(name)};"
        for name, arguments in sorted(catalog["functions"])
    ]
    indexes = [
        f"ALTER INDEX IF EXISTS {index} RENAME TO {renamed(index)};"
        for (index,) in sorted(catalog["indexes"])
    ]
    rows = ",\n".join(
        f"            ('{renamed(table)}', '{name}', '{renamed(name)}')"
        for table, name in sorted(catalog["constraints"])
    )
    constraints = [RENAME_CONSTRAINTS.format(rows=rows)] if rows else []
    policies = [
        f"DROP POLICY IF EXISTS {policy} ON {renamed(table)};"
        for table, policy in sorted(catalog["policies"])
    ]
    parameters = [
        f"DROP FUNCTION IF EXISTS {renamed(name)}({arguments});"
        for name, arguments in sorted(catalog["parameters"])
    ]
    sections = [
        ("RENAME_TABLES", len(tables), tables),
        ("RENAME_COLUMNS", len(columns), columns),
        ("RENAME_FUNCTIONS", len(functions), functions),
        ("RENAME_INDEXES", len(indexes), indexes),
        ("RENAME_CONSTRAINTS", len(catalog["constraints"]), constraints),
        ("DROP_POLICIES", len(policies), policies),
        ("DROP_RENAMED_PARAMETERS", len(parameters), parameters),
    ]
    out = []
    for name, count, statements in sections:
        out += [f"-- {name}: {count}", *statements, ""]
    return "\n".join(out)


async def _main(dsns: list[str]) -> int:
    """Read every database and print the statements covering all of them.

    :param dsns: the databases to read
    :returns: the process exit code
    """
    catalogs = [await read_catalog(dsn) for dsn in dsns]
    union = {
        section: set().union(*(catalog[section] for catalog in catalogs))
        for section in catalogs[0]
    }
    # Renamed unguarded, so a name one database lacks would fail there.
    for section in ("tables", "columns", "functions"):
        if any(catalog[section] != union[section] for catalog in catalogs):
            print(f"the databases disagree on {section}", file=sys.stderr)
            return 1
    print(render(union))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:] or [get_settings().dsn])))
