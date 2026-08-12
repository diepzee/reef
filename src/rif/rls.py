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

_EXECUTOR_ROLES = ("rif_app", "rif", "rif_probe")
"""Roles granted EXECUTE: production's constrained app role, the role that owns
the database in local dev and test, and the non-owner stand-in the test suite
uses for privilege assertions (``tests/conftest.py``; absent in production).

A fixed allowlist -- never a value from a caller -- because it is interpolated
into ``GRANT``, which rejects bind parameters. Each is granted only if it
exists in the cluster, so naming a test-only role here costs production
nothing."""

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


def _function_ddl(
    name: str,
    body: str,
    *,
    returns: str = "SETOF uuid",
    reads: tuple[str, ...] = (),
    writes: tuple[str, ...] = (),
    volatility: str = "STABLE",
    language: str = "sql",
) -> list[str]:
    """Return idempotent DDL for one helper function owned by the authz role.

    Each function is created, handed to :data:`AUTHZ_ROLE`, closed to the
    public, and opened to whichever executor roles exist. ``SET search_path``
    is fixed so a caller cannot shadow the tables the body names, which is
    mandatory for ``SECURITY DEFINER``.

    ``GRANT`` on the tables the body touches is not redundant with
    ``BYPASSRLS``: that attribute suspends *row security*, not table
    *privileges*. Without these grants the function raises "permission denied
    for table" -- confirmed against a live server, not assumed.

    :param name: the function signature, e.g. ``rif_space_ids()``
    :param body: the SQL body, without the enclosing dollar quotes
    :param returns: the ``RETURNS`` clause
    :param reads: tables the body reads, which the owner needs granted
    :param writes: tables the body modifies, needing DML beyond ``SELECT``
    :param volatility: ``STABLE`` for read-only, ``VOLATILE`` if it writes
    :param language: ``sql`` for single-statement bodies, ``plpgsql`` where
        the body needs control flow
    :returns: SQL statements to execute in order
    """
    statements = [
        (
            f"CREATE OR REPLACE FUNCTION {name} RETURNS {returns} "
            f"LANGUAGE {language} {volatility} SECURITY DEFINER "
            f"SET search_path = public, pg_catalog AS $rif${body}$rif$"
        ),
        f"ALTER FUNCTION {name} OWNER TO {AUTHZ_ROLE}",
        f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC",
    ]
    statements += [f"GRANT SELECT ON {table} TO {AUTHZ_ROLE}" for table in reads]
    statements += [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {AUTHZ_ROLE}"
        for table in writes
    ]
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


_CALLER_IS_MEMBER = (
    f"p_space IN (SELECT space_id FROM memberships WHERE person_id = {PRINCIPAL})"
)

_CALLER_IS_OWNER = (
    "EXISTS (SELECT 1 FROM spaces s WHERE s.id = p_space "
    f"AND s.owner_person_id = {PRINCIPAL})"
)


def disclosure_statements() -> list[str]:
    """Return DDL for the functions that hand out other people's details.

    This is where "only a cove's owner sees member email addresses" stops
    being a rule the web layer remembers to apply and becomes one the
    database applies. Row-level security cannot express it: a policy letting
    co-members see each other's ``persons`` rows hands over the email column
    with everything else. So the rule lives in :func:`rif_roster`, whose
    ``CASE`` returns an address only to the owner, and non-members get no
    rows at all because the membership test is part of the query.

    :func:`rif_display_names` deliberately takes any ids and checks no
    membership. Display names are already on every shared surface -- rosters,
    avatars, revision history -- and the ids are unguessable ``uuid4``s
    learned only by being in a cove. Emails and subjects never pass through
    it. It exists because revision authorship has to keep rendering names for
    people the reader cannot otherwise see.

    :returns: SQL statements to execute in order
    """
    return [
        *_function_ddl(
            "rif_roster(p_space uuid)",
            "SELECT p.display_name, "
            f"CASE WHEN {_CALLER_IS_OWNER} THEN p.email ELSE '' END "
            "FROM persons p WHERE p.id IN "
            "(SELECT person_id FROM memberships WHERE space_id = p_space) "
            f"AND {_CALLER_IS_MEMBER} ORDER BY p.display_name",
            returns="TABLE(member_name text, member_email text)",
            reads=("persons", "memberships", "spaces"),
        ),
        *_function_ddl(
            "rif_space_owner(p_space uuid)",
            "SELECT p.display_name, p.email FROM persons p JOIN spaces s "
            "ON s.owner_person_id = p.id WHERE s.id = p_space "
            f"AND {_CALLER_IS_MEMBER}",
            returns="TABLE(owner_name text, owner_email text)",
            reads=("persons", "spaces", "memberships"),
        ),
        *_function_ddl(
            "rif_display_names(p_ids uuid[])",
            "SELECT p.id, p.display_name FROM persons p WHERE p.id = ANY(p_ids)",
            returns="TABLE(person_id uuid, display_name text)",
            reads=("persons",),
        ),
        *_function_ddl(
            "rif_person_id_by_email(p_email text)",
            "SELECT id FROM persons WHERE email = lower(p_email)",
            returns="uuid",
            reads=("persons",),
        ),
        *_function_ddl(
            "rif_invites_minted(p_window_days int)",
            "SELECT count(*) FROM persons "
            f"WHERE invited_by_person_id = {PRINCIPAL} AND created_at >= "
            "LOCALTIMESTAMP - make_interval(days => p_window_days)",
            returns="bigint",
            reads=("persons",),
        ),
        *_function_ddl(
            "rif_oldest_invite(p_window_days int)",
            "SELECT min(created_at) FROM persons "
            f"WHERE invited_by_person_id = {PRINCIPAL} AND created_at >= "
            "LOCALTIMESTAMP - make_interval(days => p_window_days)",
            returns="timestamp",
            reads=("persons",),
        ),
    ]


_IDENTITY_TABLES = ("persons", "spaces", "memberships")


def identity_policy_statements() -> list[str]:
    """Return the DDL putting ``persons``, ``spaces`` and ``memberships`` under RLS.

    ``persons`` is **self only**. Not "anyone who shares a cove with me": row
    security filters rows, not columns, so any policy letting a co-member read
    another person's row hands over the email column with it, which is the
    exact disclosure this work set out to close. Names and addresses reach the
    people entitled to them through :func:`disclosure_statements`' functions,
    where the owner-only rule is a ``CASE`` rather than a caller's good
    intentions.

    ``spaces_owner_select`` looks redundant beside ``spaces_member_select``
    and is not: at creation a cove exists for an instant before its first
    membership does, and without it the membership insert below cannot see the
    space it is about to join -- so a new person's onboarding fails and they
    are locked out.

    ``spaces_member_update`` is deliberately member-scoped rather than
    owner-scoped, because ``rif.pages`` bumps ``spaces.version`` on every page
    write by any member. Row scope alone would then let a member rewrite
    ``slug``, ``kind`` or ``owner_person_id`` by direct SQL, so
    :func:`identity_grant_statements` narrows the privilege to one column.
    Rows and columns are separate axes; this needs both.

    ``memberships`` has no ``UPDATE`` policy and no owner-removes-member
    ``DELETE`` policy, on purpose -- see :func:`mutation_statements`.

    :returns: SQL statements to execute in order
    """
    owner_is_principal = f"owner_person_id = {PRINCIPAL}"
    statements: list[str] = []
    for table in _IDENTITY_TABLES:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    statements += [
        (
            f"CREATE POLICY persons_self_select ON persons FOR SELECT "
            f"USING (id = {PRINCIPAL})"
        ),
        (
            f"CREATE POLICY persons_self_update ON persons FOR UPDATE "
            f"USING (id = {PRINCIPAL}) WITH CHECK (id = {PRINCIPAL})"
        ),
        # Every invitation is pinned to the person who spent budget on it,
        # which is also what makes the budget count meaningful.
        (
            f"CREATE POLICY persons_self_delete ON persons FOR DELETE "
            f"USING (id = {PRINCIPAL})"
        ),
        (
            f"CREATE POLICY persons_invite_insert ON persons FOR INSERT "
            f"WITH CHECK (invited_by_person_id = {PRINCIPAL})"
        ),
        (
            "CREATE POLICY spaces_member_select ON spaces FOR SELECT "
            "USING (id IN (SELECT rif_space_ids()))"
        ),
        (
            f"CREATE POLICY spaces_owner_select ON spaces FOR SELECT "
            f"USING ({owner_is_principal})"
        ),
        (
            f"CREATE POLICY spaces_owner_insert ON spaces FOR INSERT "
            f"WITH CHECK ({owner_is_principal})"
        ),
        (
            "CREATE POLICY spaces_member_update ON spaces FOR UPDATE "
            "USING (id IN (SELECT rif_member_space_ids())) "
            "WITH CHECK (id IN (SELECT rif_member_space_ids()))"
        ),
        (
            f"CREATE POLICY spaces_owner_delete ON spaces FOR DELETE "
            f"USING ({owner_is_principal})"
        ),
        (
            f"CREATE POLICY memberships_self_select ON memberships FOR SELECT "
            f"USING (person_id = {PRINCIPAL})"
        ),
        # The owner arm bootstraps a cove's first membership, when no
        # membership exists yet to satisfy the member arm.
        (
            "CREATE POLICY memberships_covis_select ON memberships FOR SELECT "
            "USING (space_id IN (SELECT rif_space_ids()))"
        ),
        # Owner only. Membership is administration, and the rule is
        # creator-admin: whoever made a cove decides who is in it. An earlier
        # draft also admitted any full member, which would have let a member
        # add an arbitrary allowlisted person to a cove -- handing them every
        # page written in it, past and future -- with the application's own
        # ownership check the only thing in the way. Owner alone also covers
        # the bootstrap case, since a cove's first membership is inserted by
        # whoever just created it.
        (
            "CREATE POLICY memberships_insert ON memberships FOR INSERT "
            "WITH CHECK (rif_owns_space(space_id))"
        ),
        (
            f"CREATE POLICY memberships_self_delete ON memberships FOR DELETE "
            f"USING (person_id = {PRINCIPAL})"
        ),
    ]
    return statements


def identity_grant_statements() -> list[str]:
    """Return the column-level narrowing of ``spaces`` updates.

    The row policy has to admit every member, because a page write bumps
    ``spaces.version``. Row security cannot say *which column*, so without
    this a member could rewrite a cove's ``slug`` or hand themselves
    ``owner_person_id`` with one statement.

    Ownership transfer -- the only other legitimate update -- runs inside
    :func:`mutation_statements`' definer function, which is unaffected by a
    grant on the calling role.

    Guarded per role because a cluster has some subset of the executor roles,
    and ``REVOKE`` against a missing role is a hard error.

    :returns: SQL statements to execute in order
    """
    statements: list[str] = []
    for role in _EXECUTOR_ROLES:
        statements.append(
            f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'REVOKE UPDATE ON spaces FROM {role}'; "
            f"EXECUTE 'GRANT UPDATE (version) ON spaces TO {role}'; "
            f"END IF; END $do$"
        )
    return statements


def drop_identity_policy_statements() -> list[str]:
    """Return DDL undoing :func:`identity_policy_statements`.

    Restores the full ``UPDATE`` privilege the column grant narrowed, so a
    rollback leaves the roles as they were.

    :returns: SQL statements to execute in order
    """
    policies = {
        "persons": ("self_select", "self_update", "self_delete", "invite_insert"),
        "spaces": (
            "member_select",
            "owner_select",
            "owner_insert",
            "member_update",
            "owner_delete",
        ),
        "memberships": (
            "self_select",
            "covis_select",
            "insert",
            "self_delete",
        ),
    }
    statements: list[str] = []
    for table, names in policies.items():
        for name in names:
            suffix = name if name.startswith(table) else f"{table}_{name}"
            statements.append(f"DROP POLICY IF EXISTS {suffix} ON {table}")
    for role in _EXECUTOR_ROLES:
        statements.append(
            f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'GRANT UPDATE ON spaces TO {role}'; "
            f"END IF; END $do$"
        )
    for table in reversed(_IDENTITY_TABLES):
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return statements


def mutation_statements() -> list[str]:
    """Return DDL for the privileged writes no row policy can express.

    Three operations legitimately reach beyond what their actor can see, and
    all are administration rather than content.

    Creating an invitation is one of them, for a reason that is not obvious:
    Postgres applies **SELECT** policies to the rows an ``INSERT ...
    RETURNING`` gives back, and Piccolo's ``save()`` always returns. With
    ``persons`` self-only an inviter is therefore refused when creating an
    invitee -- even with ``persons_invite_insert`` satisfied exactly -- and
    the error names the check policy rather than the select one that actually
    denied it. Doing the insert here sidesteps the caller's policies
    altogether and keeps ``persons`` self-only, which the alternative (a
    policy letting an inviter read rows they invited) would not: that would
    expose the invitee's whole row, including the ``subject`` bound later.

    Removing a member has to decide whether the departing person is now an
    orphaned invitation to erase. That test is ``no memberships remain
    anywhere``, and the remover has no right to see memberships in coves they
    are not in -- so evaluated in Python under the policies it would come back
    short and erase somebody who is still a member elsewhere. Inside the
    function it is both correct and atomic, with no window between the check
    and the delete.

    Transferring ownership updates *another* person's membership row and the
    cove's owner. A policy permissive enough to allow that would be permissive
    enough to allow far more.

    So neither gets a policy. ``memberships`` has no ``UPDATE`` policy at all
    and no owner-removes-member ``DELETE`` policy; those paths exist only
    here, where the authority check is one line above the write and cannot be
    forgotten by a caller.

    :returns: SQL statements to execute in order
    """
    return [
        *_function_ddl(
            "rif_owns_space(p_space uuid)",
            "SELECT EXISTS (SELECT 1 FROM spaces WHERE id = p_space "
            f"AND owner_person_id = {PRINCIPAL})",
            returns="boolean",
            reads=("spaces",),
        ),
        *_function_ddl(
            "rif_allowlist_person(p_email text, p_display_name text, "
            "p_window_days int, p_budget int)",
            f"""
DECLARE
  new_id uuid;
  spent int;
BEGIN
  -- Unarmed means nobody is accountable for the invitation, and the row
  -- would land with a NULL inviter -- indistinguishable from the founding
  -- person, and outside the budget. Refuse rather than create it.
  IF {PRINCIPAL} IS NULL THEN
    RETURN NULL;
  END IF;

  -- The budget is enforced here, not by the caller. Checking it in Python
  -- and inserting afterwards is a check-then-act: two invitations racing on
  -- the last slot both see one free and both land. Locking the inviter's
  -- own row serialises those attempts, and the count and the insert then
  -- happen with nobody able to interleave.
  PERFORM 1 FROM persons WHERE id = {PRINCIPAL} FOR UPDATE;

  -- The clock is the database's. An earlier draft took it from the caller
  -- so it would agree with Python's, which made an arbitrary timestamp part
  -- of the caller's authority: backdate a row and it never counts again.
  SELECT count(*) INTO spent FROM persons
   WHERE invited_by_person_id = {PRINCIPAL}
     AND created_at >= LOCALTIMESTAMP - make_interval(days => p_window_days);
  IF spent >= p_budget THEN
    RETURN NULL;
  END IF;

  INSERT INTO persons (id, email, display_name, invited_by_person_id, created_at)
  VALUES (gen_random_uuid(), lower(p_email), p_display_name, {PRINCIPAL},
          LOCALTIMESTAMP)
  RETURNING id INTO new_id;
  RETURN new_id;
END
""",
            returns="uuid",
            writes=("persons",),
            volatility="VOLATILE",
            language="plpgsql",
        ),
        *_function_ddl(
            "rif_remove_member(p_space uuid, p_person uuid, "
            "OUT removed boolean, OUT person_erased boolean)",
            f"""
DECLARE
  affected int;
BEGIN
  removed := false;
  person_erased := false;
  -- Only the cove's owner, and never on themselves: an owner removing
  -- themselves would leave the cove with no accountable person.
  IF NOT EXISTS (SELECT 1 FROM spaces WHERE id = p_space
                 AND owner_person_id = {PRINCIPAL}) THEN
    RETURN;
  END IF;
  IF p_person = {PRINCIPAL} THEN
    RETURN;
  END IF;
  DELETE FROM memberships WHERE space_id = p_space AND person_id = p_person;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected = 0 THEN
    RETURN;
  END IF;
  removed := true;
  -- An invitation that was never taken up: no provider subject bound, and
  -- now no membership anywhere. Erasing it is the typo-repair path.
  IF EXISTS (SELECT 1 FROM persons WHERE id = p_person AND subject IS NULL)
     AND NOT EXISTS (SELECT 1 FROM memberships WHERE person_id = p_person) THEN
    DELETE FROM persons WHERE id = p_person;
    person_erased := true;
  END IF;
END
""",
            returns="record",
            writes=("memberships", "persons"),
            reads=("spaces",),
            volatility="VOLATILE",
            language="plpgsql",
        ),
        *_function_ddl(
            "rif_transfer_space_ownership(p_space uuid, p_successor uuid)",
            f"""
BEGIN
  IF NOT EXISTS (SELECT 1 FROM spaces WHERE id = p_space
                 AND owner_person_id = {PRINCIPAL}) THEN
    RETURN false;
  END IF;
  -- The successor must already belong to the cove. Without this an owner
  -- could hand a cove to any uuid that satisfies the foreign key, including
  -- somebody who has never seen it.
  IF NOT EXISTS (SELECT 1 FROM memberships WHERE space_id = p_space
                 AND person_id = p_successor) THEN
    RETURN false;
  END IF;
  UPDATE memberships SET role = 'member'
   WHERE space_id = p_space AND person_id = p_successor AND role <> 'member';
  UPDATE spaces SET owner_person_id = p_successor WHERE id = p_space;
  RETURN true;
END
""",
            returns="boolean",
            writes=("memberships", "spaces"),
            volatility="VOLATILE",
            language="plpgsql",
        ),
    ]


def drop_mutation_statements() -> list[str]:
    """Return DDL undoing :func:`mutation_statements`.

    :returns: SQL statements to execute in order
    """
    return [
        "DROP FUNCTION IF EXISTS rif_owns_space(uuid)",
        "DROP FUNCTION IF EXISTS rif_allowlist_person(text, text, int, int)",
        "DROP FUNCTION IF EXISTS rif_allowlist_person(text, text, timestamp)",
        "DROP FUNCTION IF EXISTS rif_allowlist_person(text, text)",
        "DROP FUNCTION IF EXISTS rif_remove_member(uuid, uuid)",
        "DROP FUNCTION IF EXISTS rif_transfer_space_ownership(uuid, uuid)",
    ]


def drop_disclosure_statements() -> list[str]:
    """Return DDL undoing :func:`disclosure_statements`.

    :returns: SQL statements to execute in order
    """
    return [
        "DROP FUNCTION IF EXISTS rif_roster(uuid)",
        "DROP FUNCTION IF EXISTS rif_space_owner(uuid)",
        "DROP FUNCTION IF EXISTS rif_display_names(uuid[])",
        "DROP FUNCTION IF EXISTS rif_person_id_by_email(text)",
        "DROP FUNCTION IF EXISTS rif_invites_minted(int)",
        # Earlier signatures. A changed argument list creates a *new*
        # function rather than replacing the old one, and two candidates make
        # every call ambiguous -- so the superseded shapes are dropped by
        # name, the same way _LEGACY_POLICY handles renamed policies.
        "DROP FUNCTION IF EXISTS rif_invites_minted(timestamp)",
        "DROP FUNCTION IF EXISTS rif_oldest_invite(int)",
        "DROP FUNCTION IF EXISTS rif_oldest_invite(timestamp)",
    ]


# Every identity function returns this shape and nothing wider. Not
# ``SETOF persons``: a row type would carry ``subject`` and any column added
# later, so the pre-auth path would silently regain reach it does not need.
_PERSON_ROW = "TABLE(person_id uuid, person_email text, person_display_name text)"

_PERSON_COLUMNS = "SELECT p.id, p.email, p.display_name FROM persons p"


def identity_statements() -> list[str]:
    """Return DDL for the functions that resolve an identity before arming.

    Identity binding is the one place that must read ``persons`` with no
    principal set -- resolving who the caller is *is* its job. Today it does
    that with ordinary queries, so the pre-auth path can run any ``WHERE`` it
    likes over the whole table. These functions replace that reach with four
    exact-key lookups: subject, email, or id, each returning at most one row
    and only the three columns the caller genuinely needs.

    :func:`rif_person_bind` folds the lookup and the subject-binding
    ``UPDATE`` into a single statement. Done as two steps -- find the row,
    then write to it -- two first sign-ins racing on the same invitation can
    both pass the check before either writes. As one ``UPDATE ... WHERE
    subject IS NULL RETURNING``, the loser matches no row and re-resolves by
    subject instead.

    ``lower(p_email)`` matches the application's own normalisation
    (``spaces.invite`` and ``invitations.allowlist`` both lowercase before
    storing), so a provider that varies the case of a verified address still
    binds to the invited row.

    :returns: SQL statements to execute in order
    """
    return [
        *_function_ddl(
            "rif_person_by_subject(p_subject text)",
            f"{_PERSON_COLUMNS} WHERE p.subject = p_subject",
            returns=_PERSON_ROW,
            reads=("persons",),
        ),
        *_function_ddl(
            "rif_person_by_email(p_email text)",
            f"{_PERSON_COLUMNS} WHERE p.email = lower(p_email)",
            returns=_PERSON_ROW,
            reads=("persons",),
        ),
        *_function_ddl(
            "rif_person_bind(p_email text, p_subject text)",
            "UPDATE persons SET subject = p_subject "
            "WHERE email = lower(p_email) AND subject IS NULL "
            "RETURNING id, email, display_name",
            returns=_PERSON_ROW,
            writes=("persons",),
            volatility="VOLATILE",
        ),
        *_function_ddl(
            "rif_person_alive(p_id uuid)",
            "SELECT EXISTS (SELECT 1 FROM persons WHERE id = p_id)",
            returns="boolean",
            reads=("persons",),
        ),
    ]


def drop_identity_statements() -> list[str]:
    """Return DDL undoing :func:`identity_statements`.

    :returns: SQL statements to execute in order
    """
    return [
        "DROP FUNCTION IF EXISTS rif_person_by_subject(text)",
        "DROP FUNCTION IF EXISTS rif_person_by_email(text)",
        "DROP FUNCTION IF EXISTS rif_person_bind(text, text)",
        "DROP FUNCTION IF EXISTS rif_person_alive(uuid)",
    ]


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
    statements: list[str] = (
        authz_statements()
        + identity_statements()
        + disclosure_statements()
        + mutation_statements()
    )
    for table in _TABLES:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        statements.extend(_table_policies(table))
    statements.extend(promotion_statements())
    # Identity policies last: they call the helper functions above, and
    # CREATE POLICY resolves those names at creation time.
    statements.extend(identity_policy_statements())
    statements.extend(identity_grant_statements())
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
    statements.extend(drop_identity_policy_statements())
    statements.extend(drop_mutation_statements())
    statements.extend(drop_disclosure_statements())
    statements.extend(drop_identity_statements())
    statements.extend(drop_authz_statements())
    return statements
