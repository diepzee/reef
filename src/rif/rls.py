"""Row-level security policy DDL, shared by the schema migration and tests.

The migrations under ``src/rif/piccolo_migrations/`` apply these policies to
the real database; ``tests/conftest.py`` applies them to ``rif_test`` when
building the test schema. RLS is the security boundary for this project, so
the two call sites must never be allowed to drift apart — a difference here
would mean tests validate policies that production does not actually enforce.
This module is the single source of truth both call through. (The oldest
policy migration deliberately froze its own snapshot instead: it runs before
``memberships.role`` exists, so today's predicates could not compile there.)

``NULLIF(current_setting('app.person_id', true), '')`` matters, not just a
bare ``current_setting(..., true)`` cast: a session where ``app.person_id``
was never touched returns NULL and denies as intended, but the principal
-clearing path (``set_config('app.person_id', '', true)``) sets a defined
empty string, not an absence, and ``''::uuid`` raises rather than comparing
false. ``NULLIF`` folds both cases to the same NULL, so both deny cleanly.

Every predicate reaches ``memberships`` through :func:`authz_statements`'
helper functions rather than by subquerying it. That indirection is not
stylistic. Once ``memberships`` carries its own policy, a predicate that
reads ``memberships`` is evaluated by running the ``memberships`` policy,
which reads ``memberships``, which... -- the server dies with "stack depth
limit exceeded". ``FORCE ROW LEVEL SECURITY`` closes the usual escape,
because it subjects the table *owner* to policies too, and a
``SECURITY DEFINER`` function owned by the owner is therefore no help. Only
an owner holding ``BYPASSRLS`` breaks the cycle. Hence ``rif_authz``: a
``NOLOGIN`` role that owns these functions and nothing else, so the bypass is
reachable only by calling one of them. All of this was verified against a
live server before being relied on.

Reads and writes use different predicates, and each SQL command gets its own
policy. Reads accept any membership, so a ``VIEWER`` sees everything a
``MEMBER`` sees. ``INSERT``, ``UPDATE``, and ``DELETE`` all require
``role = 'member'``, so a ``VIEWER`` membership can never change or destroy a
row even though nothing in the application yet creates viewers. The dormant
role is enforced by Postgres from day one, not bolted on later as a policy
migration. The literal is lowercase because Piccolo's ``Varchar(choices=...)``
stores an enum's *value*, not its name — these predicates and the Python
comparisons must agree on that casing, or every write would silently deny.

The per-command split is load-bearing, not stylistic. A single ``FOR ALL``
policy cannot express this: ``WITH CHECK`` constrains only the *new* row of
an ``INSERT`` or ``UPDATE``, and a ``DELETE`` has no new row, so the write
predicate would be ignored entirely and deletion would fall back to the
permissive read predicate in ``USING``. ``UPDATE`` also takes the write
predicate in ``USING``, so a viewer cannot even see the row to lock it and
the denial happens before any work.

Promotions are not content and carry their own policy: the row is a nonce
owned by one person, so :func:`promotion_statements` keys it on ownership
rather than on membership or role.
"""


def constraint_statements() -> list[str]:
    """Return DDL for constraints Piccolo's table definitions cannot express.

    Piccolo gives every table one surrogate primary key, has no syntax for
    multi-column uniqueness, and none for a partial index, so three
    constraints this schema genuinely depends on have to be stated here:
    ``memberships`` is keyed by the pair it stores, a page path is unique
    within its space, and a person owns at most one *personal* space while
    owning any number of shared ones. Losing any of them would let duplicates
    through that the application logic assumes cannot exist -- the last one
    most sharply, since a second personal space makes
    ``resolve_space(principal, "personal")`` ambiguous, which locks the person
    out of every tool call.

    :returns: SQL statements to execute in order
    """
    return [
        (
            "ALTER TABLE memberships ADD CONSTRAINT memberships_person_space "
            "UNIQUE (person_id, space_id)"
        ),
        ("ALTER TABLE pages ADD CONSTRAINT pages_space_path UNIQUE (space_id, path)"),
        (
            "CREATE UNIQUE INDEX uq_personal_owner_person ON spaces "
            "(owner_person_id) WHERE kind = 'personal'"
        ),
    ]


PRINCIPAL = "NULLIF(current_setting('app.person_id', true), '')::uuid"
"""SQL for the armed principal, or NULL when unarmed. See the module docstring."""

AUTHZ_ROLE = "rif_authz"
"""Owner of the helper functions. NOLOGIN, BYPASSRLS, owns nothing else."""

_EXECUTOR_ROLES = ("rif_app", "rif")
"""Roles granted EXECUTE: production's constrained app role, and the role that
owns the database in local dev and test. A fixed allowlist -- never a value
from a caller -- because it is interpolated into ``GRANT``, which rejects bind
parameters. Whichever of the two exists in a given cluster is granted."""

_MEMBER_PREDICATE = "space_id IN (SELECT rif_space_ids())"

_WRITE_PREDICATE = "space_id IN (SELECT rif_member_space_ids())"

# Revisions reach their space through their page. The subquery is filtered by
# ``pages``' own SELECT policy, which is this same membership test, so the
# composition is exactly the previous behaviour -- and it touches
# ``memberships`` only inside the bypassing function, never in a predicate.
_REVISION_PREDICATE = (
    "page_id IN (SELECT id FROM pages WHERE space_id IN (SELECT rif_space_ids()))"
)

_REVISION_WRITE_PREDICATE = (
    "page_id IN (SELECT id FROM pages "
    "WHERE space_id IN (SELECT rif_member_space_ids()))"
)

# Promotions belong to the person who staged them, not to a space: the row is
# a nonce, and ``section_text`` holds the exact extracted span from a personal
# page. A leaked promotion row is a leaked private paragraph, so the predicate
# is ownership rather than membership.
_PROMOTION_PREDICATE = f"person_id = {PRINCIPAL}"


def _function_ddl(name: str, body: str, *, reads: tuple[str, ...]) -> list[str]:
    """Return idempotent DDL for one helper function owned by the authz role.

    Each function is created, handed to :data:`AUTHZ_ROLE`, closed to the
    public, and opened to whichever executor roles exist. ``SET search_path``
    is fixed so a caller cannot shadow the tables the body names, which is
    mandatory for ``SECURITY DEFINER``.

    ``GRANT SELECT`` on the tables the body reads is not redundant with
    ``BYPASSRLS``: that attribute suspends *row security*, not table
    *privileges*. Without these grants the function raises "permission denied
    for table" -- confirmed against a live server, not assumed.

    :param name: the function signature, e.g. ``rif_space_ids()``
    :param body: the SQL body, without the enclosing dollar quotes
    :param reads: tables the body reads, which the owner needs granted
    :returns: SQL statements to execute in order
    """
    statements = [
        (
            f"CREATE OR REPLACE FUNCTION {name} RETURNS SETOF uuid "
            f"LANGUAGE sql STABLE SECURITY DEFINER "
            f"SET search_path = public, pg_catalog AS $rif${body}$rif$"
        ),
        f"ALTER FUNCTION {name} OWNER TO {AUTHZ_ROLE}",
        f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC",
    ]
    statements += [f"GRANT SELECT ON {table} TO {AUTHZ_ROLE}" for table in reads]
    # A cluster has either rif_app (production) or rif (dev/test), not both.
    # GRANT against a missing role is a hard error, so each is guarded.
    statements += [
        f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        f"'{role}') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION {name} TO {role}'; "
        f"END IF; END $do$"
        for role in _EXECUTOR_ROLES
    ]
    return statements


def create_authz_role_statements() -> list[str]:
    """Return DDL creating the function-owner role, for a privileged connection.

    Creating a ``BYPASSRLS`` role requires superuser, which the migration
    connection may or may not have -- on Railway it does, because the admin
    credential there is the cluster's bootstrap superuser; a properly
    least-privileged deployment would not. Callers should therefore attempt
    these and fall back to instructing an operator when the server refuses.

    ``GRANT ... TO CURRENT_USER`` is required before any
    ``ALTER FUNCTION ... OWNER TO``: Postgres will not let a role hand
    ownership to a role it is not a member of. ``GRANT CREATE ON SCHEMA`` is
    required of the *new* owner for that same reassignment.

    :returns: SQL statements to execute in order
    """
    return [
        f"CREATE ROLE {AUTHZ_ROLE} NOLOGIN BYPASSRLS",
        f"GRANT {AUTHZ_ROLE} TO CURRENT_USER",
        f"GRANT CREATE ON SCHEMA public TO {AUTHZ_ROLE}",
    ]


def authz_statements() -> list[str]:
    """Return DDL for the helper functions every policy is built on.

    Two functions, both answering "which spaces does the armed principal
    reach": any membership for reads, ``role = 'member'`` for writes. Policies
    call these instead of subquerying ``memberships`` directly, which is what
    keeps them non-recursive once ``memberships`` itself carries RLS -- a
    policy on ``memberships`` whose predicate reads ``memberships`` recurses
    until the stack is exhausted, and ``FORCE ROW LEVEL SECURITY`` means even
    the table owner cannot escape that. Only a ``BYPASSRLS`` owner can, which
    is why :data:`AUTHZ_ROLE` exists and why it owns nothing else.

    Idempotent: ``CREATE OR REPLACE`` plus guarded grants, safe to re-run.

    :returns: SQL statements to execute in order
    """
    return _function_ddl(
        "rif_space_ids()",
        f"SELECT space_id FROM memberships WHERE person_id = {PRINCIPAL}",
        reads=("memberships",),
    ) + _function_ddl(
        "rif_member_space_ids()",
        f"SELECT space_id FROM memberships WHERE person_id = {PRINCIPAL} "
        f"AND role = 'member'",
        reads=("memberships",),
    )


def drop_authz_statements() -> list[str]:
    """Return DDL undoing :func:`authz_statements`.

    The role itself is left alone: it is created out of band by
    ``scripts/provision_app_role.py`` (creating a ``BYPASSRLS`` role needs
    privileges migrations deliberately do not have) and may own functions from
    a later migration.

    :returns: SQL statements to execute in order
    """
    return [
        "DROP FUNCTION IF EXISTS rif_space_ids()",
        "DROP FUNCTION IF EXISTS rif_member_space_ids()",
    ]


_TABLES = ("pages", "attachments", "revisions")
_COMMANDS = ("select", "insert", "update", "delete")

_LEGACY_POLICY = "{table}_member"
"""The single ``FOR ALL`` policy name the pre-split shape created.

``disable_statements`` still drops it: a migration that replaces policies in
place runs against a database that carries the old name, and a silently no-op
drop would leave the permissive policy alive alongside the new ones — Postgres
ORs permissive policies, so the weaker one would win.
"""


def _predicates(table: str) -> tuple[str, str]:
    """Return the read and write predicates for one content table.

    :param table: ``pages``, ``attachments``, or ``revisions``
    :returns: the read predicate and the write predicate
    """
    if table == "revisions":
        return _REVISION_PREDICATE, _REVISION_WRITE_PREDICATE
    return _MEMBER_PREDICATE, _WRITE_PREDICATE


def _table_policies(table: str) -> list[str]:
    """Return the per-command policy DDL for one content table.

    :param table: ``pages``, ``attachments``, or ``revisions``
    :returns: SQL statements to execute in order
    """
    read, write = _predicates(table)
    return [
        f"CREATE POLICY {table}_select ON {table} FOR SELECT USING ({read})",
        f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({write})",
        (
            f"CREATE POLICY {table}_update ON {table} FOR UPDATE "
            f"USING ({write}) WITH CHECK ({write})"
        ),
        f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING ({write})",
    ]


def promotion_statements() -> list[str]:
    """Return idempotent DDL arming RLS on ``promotions``.

    Split out from :func:`enable_statements` because the table went live
    without a policy and needed a follow-up migration against databases that
    already existed. ``ENABLE``/``FORCE`` are no-ops when already set, and the
    policy is dropped before creation, so this is safe to re-run.

    :returns: SQL statements to execute in order
    """
    return [
        "ALTER TABLE promotions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE promotions FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS promotions_owner ON promotions",
        (
            f"CREATE POLICY promotions_owner ON promotions "
            f"USING ({_PROMOTION_PREDICATE}) WITH CHECK ({_PROMOTION_PREDICATE})"
        ),
    ]


def enable_statements() -> list[str]:
    """Return the DDL that turns on and enforces RLS on the content tables.

    Covers ``pages`` and ``attachments`` (membership via their own
    ``space_id``) and ``revisions`` (membership via their page's space), each
    with one policy per SQL command, and then ``promotions`` (ownership).
    ``FORCE ROW LEVEL SECURITY`` extends the policies to the table owner, not
    just other roles.

    The helper functions come first: every predicate below calls them, and
    ``CREATE POLICY`` resolves the name at creation time.

    :returns: SQL statements to execute in order
    """
    statements: list[str] = authz_statements()
    for table in _TABLES:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        statements.extend(_table_policies(table))
    statements.extend(promotion_statements())
    return statements


def disable_statements() -> list[str]:
    """Return the DDL that undoes :func:`enable_statements`.

    Statements run in the reverse order of ``enable_statements``: drop every
    policy this module has ever created for a table — the four per-command
    ones and the legacy ``FOR ALL`` name they replaced — before turning
    enforcement and row security back off, and the helper functions last of
    all, once nothing refers to them.

    :returns: SQL statements to execute in order
    """
    statements: list[str] = [
        "DROP POLICY IF EXISTS promotions_owner ON promotions",
        "ALTER TABLE promotions NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE promotions DISABLE ROW LEVEL SECURITY",
    ]
    for table in reversed(_TABLES):
        for command in _COMMANDS:
            statements.append(f"DROP POLICY IF EXISTS {table}_{command} ON {table}")
        statements.append(
            f"DROP POLICY IF EXISTS {_LEGACY_POLICY.format(table=table)} ON {table}"
        )
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    statements.extend(drop_authz_statements())
    return statements
