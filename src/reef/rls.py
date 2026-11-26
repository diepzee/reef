"""Row-level security policy DDL, shared by the schema migration and tests.

``tests/conftest.py`` applies these policies to ``reef_test`` when building
the test schema, straight from this module. Migrations apply them to the real
database as they stood on the day each migration was written: the oldest
froze their own snapshots, those older than ``2026-11-20T10:00:00`` call
:mod:`reef.frozen_ddl`, and ``2026-11-20T10:00:00`` carries the post-rename
DDL as fixed text. A migration that read this module at run time would apply
whatever it says on the day a database is built, which reaches fresh builds
and never production. So a change here needs a migration of its own.

RLS is the security boundary for this project, so the two must never drift
apart — a difference would mean tests validate policies that production does
not actually enforce. ``tests/test_migration_chain.py`` builds a database by
running every migration and fails if its policies differ from the suite's.

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
an owner holding ``BYPASSRLS`` breaks the cycle. Hence ``reef_authz``: a
``NOLOGIN`` role that owns these functions and nothing else, so the bypass is
reachable only by calling one of them. All of this was verified against a
live server before being relied on.

Reads and writes use different predicates, and each SQL command gets its own
policy. Reads accept any membership, so a ``VIEWER`` sees everything a
``MEMBER`` sees. ``INSERT``, ``UPDATE``, and ``DELETE`` all require
``role = 'member'``, so a ``VIEWER`` membership can never change or destroy a
row. Invites create viewers on purpose since Aug 2026; the enforcement was
never bolted on, because Postgres carried it from day one. The literal is lowercase because Piccolo's ``Varchar(choices=...)``
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
    ``memberships`` is keyed by the pair it stores, an engram path is unique
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
        # The rule the tool surface actually depends on: whatever a person
        # types resolves to exactly one space. Stated as a property of the
        # person, because that is what it is -- a space's name being unique
        # *globally* would be a far stronger claim, and was the one that let
        # anybody squat 'family' for every future user.
        (
            "ALTER TABLE memberships ADD CONSTRAINT memberships_person_alias "
            "UNIQUE (person_id, alias)"
        ),
        (
            "ALTER TABLE engrams ADD CONSTRAINT engrams_space_path UNIQUE (space_id, path)"
        ),
        (
            "CREATE UNIQUE INDEX uq_personal_owner_person ON spaces "
            "(owner_person_id) WHERE kind = 'personal'"
        ),
    ]


PRINCIPAL = "NULLIF(current_setting('app.person_id', true), '')::uuid"
"""SQL for the armed principal, or NULL when unarmed. See the module docstring."""

AUTHZ_ROLE = "reef_authz"
"""What a cluster created from now on names the function-owner role."""

FORMER_AUTHZ_ROLE = "rif_authz"
"""What clusters created before the rename call it.

Renaming it is an operator step run out of band: the migration credential
cannot ``ALTER ROLE`` -- the 12 August migration says as much about creating
this role in the first place. So the rename lands on the operator's
schedule, not a deploy's, and the DDL below has to work on either side of it.

Safe to rename precisely because it is ``NOLOGIN``: nothing connects as it,
so no credential names it. That is what separates it from ``reef``/``reef_app``
/``reef_probe``, whose names live in connection strings and cannot move
without the connection string moving in the same breath."""


def authz_role_expression() -> str:
    """Return SQL evaluating to this cluster's authz role name.

    Prefers the new name, falls back to the old, and defaults to the new so
    a cluster with neither -- one about to have the role created -- still
    yields a usable name rather than NULL. A NULL would reach Postgres as
    ``OWNER TO ""`` and fail somewhere far from the cause.

    :returns: a SQL scalar expression returning ``text``
    """
    return (
        "COALESCE("
        f"(SELECT rolname FROM pg_roles WHERE rolname = '{AUTHZ_ROLE}'), "
        f"(SELECT rolname FROM pg_roles WHERE rolname = '{FORMER_AUTHZ_ROLE}'), "
        f"'{AUTHZ_ROLE}')"
    )


def _for_authz(template: str) -> str:
    """Wrap a statement whose one ``%I`` placeholder is the authz role.

    The name is resolved inside the server rather than baked in here, so the
    same DDL applies before and after an operator renames the role.

    :param template: a ``format()`` template with a single ``%I``
    :returns: a DO block that resolves the role and executes the statement
    """
    literal = template.replace("'", "''")
    return (
        f"DO $authz$ DECLARE authz text := {authz_role_expression()}; "
        f"BEGIN EXECUTE format('{literal}', authz); END $authz$"
    )


"""Owner of the helper functions. NOLOGIN, BYPASSRLS, owns nothing else."""

_EXECUTOR_ROLES = (
    "reef_app",
    "reef",
    "reef_probe",
    "rif_app",
    "rif",
    "rif_probe",
)
"""Roles granted EXECUTE: production's constrained app role, the role that owns
the database in local dev and test, and the non-owner stand-in the test suite
uses for privilege assertions (``tests/conftest.py``; absent in production).

Both spellings are listed because renaming a login role means renaming it in
the connection string in the same breath -- an operator step with a
maintenance window, not something a deploy can do. Listing both means the
application does not care which side of that window it is running on.

A fixed allowlist -- never a value from a caller -- because it is interpolated
into ``GRANT``, which rejects bind parameters. Each is granted only if it
exists in the cluster, so naming a role no cluster has costs nothing."""

_MEMBER_PREDICATE = "space_id IN (SELECT reef_space_ids())"

_WRITE_PREDICATE = "space_id IN (SELECT reef_member_space_ids())"

# Revisions reach their space through their engram. The subquery is filtered by
# ``engrams``' own SELECT policy, which is this same membership test, so the
# composition is exactly the previous behaviour -- and it touches
# ``memberships`` only inside the bypassing function, never in a predicate.
_REVISION_PREDICATE = (
    "engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_space_ids()))"
)

_REVISION_WRITE_PREDICATE = "engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids()))"

# Promotions belong to the person who staged them, not to a space: the row is
# a nonce, and ``section_text`` holds the exact extracted span from a personal
# engram. A leaked promotion row is a leaked private paragraph, so the predicate
# is ownership rather than membership.
_PROMOTION_PREDICATE = f"person_id = {PRINCIPAL}"


HELPER_SEARCH_PATH = "pg_catalog, public, pg_temp"
"""Where a helper function looks names up, in this order.

The system catalog comes first, so an object in ``public`` cannot stand in
for a built-in such as ``current_setting``. Temporary tables come last and
are named on purpose: left out, Postgres searches them *first*, and a
session's own temporary ``memberships`` table would answer for the real one.
``tests/test_helper_search_path.py`` shows both.
"""


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
    is fixed to :data:`HELPER_SEARCH_PATH`, so a caller cannot shadow the
    tables or the built-ins the body names, which is mandatory for
    ``SECURITY DEFINER``.

    ``GRANT`` on the tables the body touches is not redundant with
    ``BYPASSRLS``: that attribute suspends *row security*, not table
    *privileges*. Without these grants the function raises "permission denied
    for table" -- confirmed against a live server, not assumed.

    :param name: the function signature, e.g. ``reef_space_ids()``
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
            f"SET search_path = {HELPER_SEARCH_PATH} AS $reef${body}$reef$"
        ),
        _for_authz(f"ALTER FUNCTION {name} OWNER TO %I"),
        f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC",
    ]
    statements += [_for_authz(f"GRANT SELECT ON {table} TO %I") for table in reads]
    statements += [
        _for_authz(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO %I")
        for table in writes
    ]
    # A cluster has either reef_app (production) or reef (dev/test), not both.
    # GRANT against a missing role is a hard error, so each is guarded.
    statements += [
        f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        f"'{role}') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION {name} TO {role}'; "
        f"END IF; END $do$"
        for role in _EXECUTOR_ROLES
    ]
    return statements


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
        "reef_space_ids()",
        f"SELECT space_id FROM memberships WHERE person_id = {PRINCIPAL}",
        reads=("memberships",),
    ) + _function_ddl(
        "reef_member_space_ids()",
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

    This is where "only a space's owner sees member email addresses" stops
    being a rule the web layer remembers to apply and becomes one the
    database applies. Row-level security cannot express it: a policy letting
    co-members see each other's ``persons`` rows hands over the email column
    with everything else. So the rule lives in :func:`reef_roster`, whose
    ``CASE`` returns an address only to the owner, and non-members get no
    rows at all because the membership test is part of the query.

    :func:`reef_display_names` deliberately takes any ids and checks no
    membership. Display names are already on every shared surface -- rosters,
    avatars, revision history -- and the ids are unguessable ``uuid4``s
    learned only by being in a space. Emails and subjects never pass through
    it. It exists because revision authorship has to keep rendering names for
    people the reader cannot otherwise see.

    ``reef_roster`` returns ``person_id`` on the same reasoning. A roster row
    needs a stable key its viewer can actually hold: ``email`` is blanked to
    ``''`` for everyone but the owner, so keying on it leaves non-owners with
    no key at all -- which is precisely who needs one to fetch a co-member's
    picture (:func:`avatar_statements`).

    :returns: SQL statements to execute in order
    """
    return [
        *_function_ddl(
            "reef_roster(p_space uuid)",
            "SELECT p.id, p.display_name, "
            f"CASE WHEN {_CALLER_IS_OWNER} THEN p.email ELSE '' END "
            "FROM persons p WHERE p.id IN "
            "(SELECT person_id FROM memberships WHERE space_id = p_space) "
            f"AND {_CALLER_IS_MEMBER} ORDER BY p.display_name",
            returns="TABLE(person_id uuid, member_name text, member_email text)",
            reads=("persons", "memberships", "spaces"),
        ),
        *_function_ddl(
            "reef_space_owner(p_space uuid)",
            "SELECT p.display_name, p.email FROM persons p JOIN spaces s "
            "ON s.owner_person_id = p.id WHERE s.id = p_space "
            f"AND {_CALLER_IS_MEMBER}",
            returns="TABLE(owner_name text, owner_email text)",
            reads=("persons", "spaces", "memberships"),
        ),
        *_function_ddl(
            "reef_display_names(p_ids uuid[])",
            "SELECT p.id, p.display_name FROM persons p WHERE p.id = ANY(p_ids)",
            returns="TABLE(person_id uuid, display_name text)",
            reads=("persons",),
        ),
        *_function_ddl(
            "reef_person_id_by_email(p_email text)",
            "SELECT id FROM persons WHERE email = lower(p_email)",
            returns="uuid",
            reads=("persons",),
        ),
        *_function_ddl(
            "reef_invites_minted(p_window_days int)",
            "SELECT count(*) FROM persons "
            f"WHERE invited_by_person_id = {PRINCIPAL} AND created_at >= "
            "LOCALTIMESTAMP - make_interval(days => p_window_days)",
            returns="bigint",
            reads=("persons",),
        ),
        *_function_ddl(
            "reef_oldest_invite(p_window_days int)",
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

    ``persons`` is **self only**. Not "anyone who shares a space with me": row
    security filters rows, not columns, so any policy letting a co-member read
    another person's row hands over the email column with it, which is the
    exact disclosure this work set out to close. Names and addresses reach the
    people entitled to them through :func:`disclosure_statements`' functions,
    where the owner-only rule is a ``CASE`` rather than a caller's good
    intentions.

    ``spaces_owner_select`` looks redundant beside ``spaces_member_select``
    and is not: at creation a space exists for an instant before its first
    membership does, and without it the membership insert below cannot see the
    space it is about to join -- so a new person's onboarding fails and they
    are locked out.

    ``spaces_member_update`` is deliberately member-scoped rather than
    owner-scoped, because ``reef.engrams`` bumps ``spaces.version`` on every engram
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
            "USING (id IN (SELECT reef_space_ids()))"
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
            "USING (id IN (SELECT reef_member_space_ids())) "
            "WITH CHECK (id IN (SELECT reef_member_space_ids()))"
        ),
        (
            f"CREATE POLICY spaces_owner_delete ON spaces FOR DELETE "
            f"USING ({owner_is_principal})"
        ),
        (
            f"CREATE POLICY memberships_self_select ON memberships FOR SELECT "
            f"USING (person_id = {PRINCIPAL})"
        ),
        # The owner arm bootstraps a space's first membership, when no
        # membership exists yet to satisfy the member arm.
        (
            "CREATE POLICY memberships_covis_select ON memberships FOR SELECT "
            "USING (space_id IN (SELECT reef_space_ids()))"
        ),
        # Owner only. Membership is administration, and the rule is
        # creator-admin: whoever made a space decides who is in it. An earlier
        # draft also admitted any full member, which would have let a member
        # add an arbitrary allowlisted person to a space -- handing them every
        # engram written in it, past and future -- with the application's own
        # ownership check the only thing in the way. Owner alone also covers
        # the bootstrap case, since a space's first membership is inserted by
        # whoever just created it.
        (
            "CREATE POLICY memberships_insert ON memberships FOR INSERT "
            "WITH CHECK (reef_owns_space(space_id))"
        ),
        (
            f"CREATE POLICY memberships_self_delete ON memberships FOR DELETE "
            f"USING (person_id = {PRINCIPAL})"
        ),
        # Renaming a space is editing your own membership, and nobody else's.
        # The row scope is here; the *column* scope is a grant, because row
        # security cannot say "alias but not role" -- without that half this
        # policy would also let a viewer promote themselves to member.
        (
            f"CREATE POLICY memberships_self_update ON memberships FOR UPDATE "
            f"USING (person_id = {PRINCIPAL}) WITH CHECK (person_id = {PRINCIPAL})"
        ),
    ]
    return statements


def identity_grant_statements() -> list[str]:
    """Return the column-level narrowing of ``spaces`` updates.

    The row policy has to admit every member, because an engram write bumps
    ``spaces.version``. Row security cannot say *which column*, so without
    this a member could rewrite a space's ``slug`` or hand themselves
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


def alias_statements() -> list[str]:
    """Return DDL for everything that names ``memberships.alias``.

    Split out of :func:`mutation_statements` and
    :func:`identity_grant_statements` for the reason
    :func:`session_epoch_statements` gives at length: three August migrations
    call :func:`enable_statements`, all of them running before ``alias``
    exists, and a plpgsql body naming a missing column is refused at creation
    time. Every database built from scratch died here with ``column "alias"
    of relation "memberships" does not exist``.

    Called by the migration that adds the column, and by the test suite's
    schema fixture.

    :returns: SQL statements to execute in order
    """
    statements: list[str] = [
        # Dropped immediately before the four-argument version is created,
        # not only in mutation_statements. The creation lives here now (the
        # body names memberships.alias, which the older migrations predate)
        # while that drop sits in another group, so the two can run at
        # different times -- and a database carrying both signatures makes
        # every call to either one ambiguous. Adjacent, they cannot. A fresh
        # chain never created the three-argument form and this is a no-op.
        "DROP FUNCTION IF EXISTS reef_admit_member(uuid, uuid, text)",
    ]
    statements += list(
        _function_ddl(
            "reef_admit_member(p_space uuid, p_person uuid, p_alias text, p_role text)",
            f"""
DECLARE
  candidate text;
  suffix int := 1;
BEGIN
  -- Only the space's owner admits anybody, matching memberships_insert.
  IF NOT EXISTS (SELECT 1 FROM spaces WHERE id = p_space
                 AND owner_person_id = {PRINCIPAL}) THEN
    RETURN NULL;
  END IF;
  -- The role is decided here, where the row is written, so no caller can
  -- smuggle in a third value the write policies have never heard of.
  IF p_role NOT IN ('member', 'viewer') THEN
    RETURN NULL;
  END IF;
  IF EXISTS (SELECT 1 FROM memberships WHERE space_id = p_space
             AND person_id = p_person) THEN
    RETURN NULL;
  END IF;
  -- The alias has to be free for the *invitee*, and the owner cannot see
  -- the invitee's other memberships -- persons are self-only and
  -- memberships are visible per space, so evaluated in Python this check
  -- would come back clear and the insert would hit the unique constraint.
  -- In here it is both correct and atomic: no window between choosing a
  -- name and taking it.
  --
  -- 'personal' is refused outright rather than suffixed. It is the one
  -- alias whose meaning is fixed for everybody, and a space admitted under
  -- it would shadow the private space in every later call.
  candidate := p_alias;
  IF candidate = 'personal' OR candidate IS NULL OR candidate = '' THEN
    candidate := 'space';
  END IF;
  WHILE EXISTS (SELECT 1 FROM memberships WHERE person_id = p_person
                AND alias = candidate) LOOP
    suffix := suffix + 1;
    candidate := p_alias || '-' || suffix;
    -- A person with thousands of spaces is not a case worth looping over.
    IF suffix > 999 THEN
      RETURN NULL;
    END IF;
  END LOOP;
  INSERT INTO memberships (person_id, space_id, role, alias)
  VALUES (p_person, p_space, p_role, candidate);
  RETURN candidate;
END
""",
            returns="text",
            writes=("memberships",),
            reads=("spaces",),
            volatility="VOLATILE",
            language="plpgsql",
        )
    )
    # memberships_self_update admits the whole row; only the alias may
    # actually move. role and space_id stay out of reach of the app role, so a
    # member cannot promote themselves or graft their membership onto another
    # space. Ownership transfer changes role from inside a definer function,
    # which a grant on the caller does not constrain.
    statements += [
        f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        f"'{role}') THEN EXECUTE 'REVOKE UPDATE ON memberships FROM {role}'; "
        f"EXECUTE 'GRANT UPDATE (alias) ON memberships TO {role}'; "
        f"END IF; END $do$"
        for role in _EXECUTOR_ROLES
    ]
    return statements


#: What a person may rewrite on their own ``persons`` row. An allowlist, so a
#: column added later arrives unwritable and says so, rather than arriving
#: writable and saying nothing.
_PERSON_SELF_COLUMNS = "display_name, avatar_mime, avatar_bytes, session_epoch"


def person_column_grant_statements() -> list[str]:
    """Return the column-level narrowing of ``persons`` updates.

    ``persons_self_update`` admits the whole of a person's own row, because
    row security can say *which row* and never *which column*. That was
    harmless while every column on the row was theirs to change. It stopped
    being harmless when ``joined_open_door`` arrived: the launch ceiling is
    counted over that flag, and a member who can set it on themselves can
    burn seats on rows that never came through the door.

    Not an escalation -- nobody gains reach over anybody else's memory -- but
    the ceiling is the one number the open door leans on, and it should not
    be writable by the population it exists to bound.

    ``email`` and ``subject`` are outside the grant too, which they always
    should have been: they are the identity binding, rewritten only by the
    definer functions in :func:`identity_statements`, and a grant on the
    calling role does not constrain those.

    Kept out of :func:`enable_statements` for the reason
    :func:`open_door_statements` gives: it names a column that historical
    migrations predate.

    :returns: SQL statements to execute in order
    """
    return [
        f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        f"'{role}') THEN EXECUTE 'REVOKE UPDATE ON persons FROM {role}'; "
        f"EXECUTE 'GRANT UPDATE ({_PERSON_SELF_COLUMNS}) ON persons TO {role}'; "
        f"END IF; END $do$"
        for role in _EXECUTOR_ROLES
    ]


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
            "self_update",
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
            f"EXECUTE 'GRANT UPDATE ON memberships TO {role}'; "
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
    anywhere``, and the remover has no right to see memberships in spaces they
    are not in -- so evaluated in Python under the policies it would come back
    short and erase somebody who is still a member elsewhere. Inside the
    function it is both correct and atomic, with no window between the check
    and the delete.

    Transferring ownership updates *another* person's membership row and the
    space's owner. A policy permissive enough to allow that would be permissive
    enough to allow far more.

    So neither gets a policy. ``memberships`` has no ``UPDATE`` policy at all
    and no owner-removes-member ``DELETE`` policy; those paths exist only
    here, where the authority check is one line above the write and cannot be
    forgotten by a caller.

    :returns: SQL statements to execute in order
    """
    return [
        # ``BYPASSRLS`` suspends row security, not privileges, and a table
        # grant does not reach the sequence behind a serial key. Without
        # this, ``reef_admit_member``'s INSERT into ``memberships`` fails with
        # "permission denied for sequence" -- which reads as a policy
        # refusal and is not one.
        _for_authz("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I"),
        # The three-argument reef_admit_member predates the role parameter.
        # CREATE OR REPLACE cannot change a signature, and leaving the old
        # function standing would make every call ambiguous -- so it is
        # dropped wherever this group runs. A fresh chain never created it
        # and the drop is a no-op there.
        "DROP FUNCTION IF EXISTS reef_admit_member(uuid, uuid, text)",
        # Policies call this one, so it is replaced in place and never
        # dropped the way the others can be: dropping it needs CASCADE, which
        # takes the policies with it. That also fixes its parameter's name,
        # since CREATE OR REPLACE refuses to rename a parameter. The name is
        # invisible: every caller passes positionally.
        *_function_ddl(
            "reef_owns_space(p_space uuid)",
            "SELECT EXISTS (SELECT 1 FROM spaces WHERE id = p_space "
            f"AND owner_person_id = {PRINCIPAL})",
            returns="boolean",
            reads=("spaces",),
        ),
        *_function_ddl(
            "reef_allowlist_person(p_email text, p_display_name text, "
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
            "reef_remove_member(p_space uuid, p_person uuid, "
            "OUT removed boolean, OUT person_erased boolean)",
            f"""
DECLARE
  affected int;
BEGIN
  removed := false;
  person_erased := false;
  -- Only the space's owner, and never on themselves: an owner removing
  -- themselves would leave the space with no accountable person.
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
            "reef_transfer_space_ownership(p_space uuid, p_successor uuid)",
            f"""
BEGIN
  IF NOT EXISTS (SELECT 1 FROM spaces WHERE id = p_space
                 AND owner_person_id = {PRINCIPAL}) THEN
    RETURN false;
  END IF;
  -- The successor must already belong to the space. Without this an owner
  -- could hand a space to any uuid that satisfies the foreign key, including
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
        "DROP FUNCTION IF EXISTS reef_owns_space(uuid)",
        "DROP FUNCTION IF EXISTS reef_allowlist_person(text, text, int, int)",
        "DROP FUNCTION IF EXISTS reef_allowlist_person(text, text, timestamp)",
        "DROP FUNCTION IF EXISTS reef_allowlist_person(text, text)",
        "DROP FUNCTION IF EXISTS reef_admit_member(uuid, uuid, text)",
        "DROP FUNCTION IF EXISTS reef_admit_member(uuid, uuid, text, text)",
        "DROP FUNCTION IF EXISTS reef_remove_member(uuid, uuid)",
        "DROP FUNCTION IF EXISTS reef_transfer_space_ownership(uuid, uuid)",
    ]


def drop_disclosure_statements() -> list[str]:
    """Return DDL undoing :func:`disclosure_statements`.

    :returns: SQL statements to execute in order
    """
    return [
        "DROP FUNCTION IF EXISTS reef_roster(uuid)",
        "DROP FUNCTION IF EXISTS reef_space_owner(uuid)",
        "DROP FUNCTION IF EXISTS reef_display_names(uuid[])",
        "DROP FUNCTION IF EXISTS reef_person_id_by_email(text)",
        "DROP FUNCTION IF EXISTS reef_invites_minted(int)",
        # Earlier signatures. A changed argument list creates a *new*
        # function rather than replacing the old one, and two candidates make
        # every call ambiguous -- so the superseded shapes are dropped by
        # name, the same way _LEGACY_POLICY handles renamed policies.
        "DROP FUNCTION IF EXISTS reef_invites_minted(timestamp)",
        "DROP FUNCTION IF EXISTS reef_oldest_invite(int)",
        "DROP FUNCTION IF EXISTS reef_oldest_invite(timestamp)",
    ]


# Every identity function returns this shape and nothing wider. Not
# ``SETOF persons``: a row type would carry ``subject`` and any column added
# later, so the pre-auth path would silently regain reach it does not need.
_PERSON_ROW = "TABLE(person_id uuid, person_email text, person_display_name text)"

_PERSON_COLUMNS = "SELECT p.id, p.email, p.display_name FROM persons p"

#: Advisory-lock key serialising open-door admissions. Arbitrary, but fixed:
#: two admissions serialise only if they contend on the same key.
_SEAT_LOCK_KEY = 0x0DD00DD0


def identity_statements() -> list[str]:
    """Return DDL for the functions that resolve an identity before arming.

    Identity binding is the one place that must read ``persons`` with no
    principal set -- resolving who the caller is *is* its job. Today it does
    that with ordinary queries, so the pre-auth path can run any ``WHERE`` it
    likes over the whole table. These functions replace that reach with four
    exact-key lookups: subject, email, or id, each returning at most one row
    and only the three columns the caller genuinely needs.

    :func:`reef_person_bind` folds the lookup and the subject-binding
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
            "reef_person_by_subject(p_subject text)",
            f"{_PERSON_COLUMNS} WHERE p.subject = p_subject",
            returns=_PERSON_ROW,
            reads=("persons",),
        ),
        *_function_ddl(
            "reef_person_by_email(p_email text)",
            f"{_PERSON_COLUMNS} WHERE p.email = lower(p_email)",
            returns=_PERSON_ROW,
            reads=("persons",),
        ),
        *_function_ddl(
            "reef_person_bind(p_email text, p_subject text)",
            "UPDATE persons SET subject = p_subject "
            "WHERE email = lower(p_email) AND subject IS NULL "
            "RETURNING id, email, display_name",
            returns=_PERSON_ROW,
            writes=("persons",),
            volatility="VOLATILE",
        ),
        *_function_ddl(
            "reef_person_alive(p_id uuid)",
            "SELECT EXISTS (SELECT 1 FROM persons WHERE id = p_id)",
            returns="boolean",
            reads=("persons",),
        ),
    ]


def session_epoch_statements() -> list[str]:
    """Return DDL for the function that reads a person's session epoch.

    Answers both questions the request path asks of a sealed cookie -- does
    this person still exist, and has their session been revoked -- in one
    round trip, before any principal is armed. NULL means the row is gone,
    which denies exactly as a mismatched epoch does.

    Split out of :func:`identity_statements` because it names
    ``persons.session_epoch``, and three historical migrations call
    :func:`enable_statements` to re-apply the policies of their day, all of
    them running before that column exists. A ``LANGUAGE sql`` body is parsed
    and validated when the function is created, so this failed every database
    built from scratch with ``column "session_epoch" does not exist`` --
    a fresh deploy, and the restore drill in ``docs/restore.md``. Production
    never saw it, being long past those migrations.

    This is the same shape :func:`appearance_statements` documents, and the
    third time this project has hit it. ``tests/test_open_door.py`` now
    asserts the rule directly.

    :returns: SQL statements to execute in order
    """
    return _function_ddl(
        "reef_person_session_epoch(p_id uuid)",
        "SELECT session_epoch FROM persons WHERE id = p_id",
        returns="integer",
        reads=("persons",),
    )


def drop_identity_statements() -> list[str]:
    """Return DDL undoing :func:`identity_statements`.

    :returns: SQL statements to execute in order
    """
    return [
        "DROP FUNCTION IF EXISTS reef_person_by_subject(text)",
        "DROP FUNCTION IF EXISTS reef_person_by_email(text)",
        "DROP FUNCTION IF EXISTS reef_person_bind(text, text)",
        "DROP FUNCTION IF EXISTS reef_person_alive(uuid)",
        "DROP FUNCTION IF EXISTS reef_person_session_epoch(uuid)",
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
        "DROP FUNCTION IF EXISTS reef_space_ids()",
        "DROP FUNCTION IF EXISTS reef_member_space_ids()",
    ]


_TABLES = ("engrams", "attachments", "revisions")
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

    :param table: ``engrams``, ``attachments``, or ``revisions``
    :returns: the read predicate and the write predicate
    """
    if table == "revisions":
        return _REVISION_PREDICATE, _REVISION_WRITE_PREDICATE
    return _MEMBER_PREDICATE, _WRITE_PREDICATE


def _table_policies(table: str) -> list[str]:
    """Return the per-command policy DDL for one content table.

    :param table: ``engrams``, ``attachments``, or ``revisions``
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


def appearance_statements() -> list[str]:
    """Return idempotent DDL arming RLS on ``space_appearances``.

    Deliberately **not** called from :func:`enable_statements`, unlike every
    other group here. Two historical migrations (``2026-08-08T10:00:00`` and
    ``2026-08-12T09:00:00``) call ``enable_statements`` to re-apply the
    policies of their day, and they run long before ``space_appearances``
    exists. Putting this in there breaks the chain on any database built from
    scratch -- a fresh deploy, or the restore drill in ``docs/restore.md`` --
    with ``relation "space_appearances" does not exist``. Production would
    never have noticed, having those migrations already behind it.

    So the two callers name it explicitly: the migration that creates the
    table, and the test suite's schema fixture. Anything adding a table from
    here on wants the same treatment.

    One ``FOR ALL`` policy, which is the right shape here and nowhere else
    in this module: the table holds only a viewer's own display preferences,
    so there is no column whose write needs narrowing and no row anybody but
    its owner should ever see. ``person_id = principal`` on both sides means
    a person can read and write their own choices and cannot observe, let
    alone change, how anyone else sees the same space.

    Deliberately no membership test. Losing access to a space should not make
    the row unreachable -- it would strand a row the person can no longer
    delete -- and a preference for a space you cannot see discloses nothing,
    since the space itself stays invisible.

    :returns: SQL statements to execute in order
    """
    predicate = f"person_id = {PRINCIPAL}"
    return [
        "ALTER TABLE space_appearances ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE space_appearances FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS space_appearances_self ON space_appearances",
        (
            f"CREATE POLICY space_appearances_self ON space_appearances "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        ),
    ]


def open_door_statements() -> list[str]:
    """Return DDL for the launch exception's admission function.

    Deliberately **not** called from :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives at length: historical migrations call
    that to re-apply the policies of their day, and they run long before
    ``persons.joined_open_door`` exists. Putting this there fails every
    database built from scratch with ``column "joined_open_door" does not
    exist``, while production -- already past those migrations -- would never
    have noticed. The migration that adds the column names it explicitly, as
    does the test suite's schema fixture.

    ``reef_open_door_admit`` is the mirror image of ``reef_allowlist_person``.
    That one refuses to run unarmed, because a row minted with no principal
    would have a NULL inviter and nobody accountable for it. This one *only*
    ever runs unarmed: it is reached from the OIDC callback, before any
    principal can exist, which is the whole difficulty of admitting a
    stranger. What replaces accountability is the flag and the ceiling.

    :returns: SQL statements to execute in order
    """
    return _function_ddl(
        "reef_open_door_admit(p_email text, p_subject text, "
        "p_display_name text, p_seats int)",
        f"""
DECLARE
  bound_id uuid;
  bound_email text;
  bound_name text;
  taken int;
BEGIN
  -- Serialises admissions. The ceiling is a global count, so the check and
  -- the insert have to be uninterleaved or two callers racing for the last
  -- seat both see it free and both land -- the same check-then-act
  -- reef_allowlist_person locks the inviter's row to avoid. There is no
  -- per-caller row to lock here, the counted set being every open-door row
  -- at once, so the lock is advisory and taken on a fixed key. It releases
  -- when the transaction ends, however it ends.
  PERFORM pg_advisory_xact_lock({_SEAT_LOCK_KEY});

  -- An address already on the allowlist is somebody a member invited for
  -- real, possibly in the seconds between the callback and the click.
  -- Binding it is right; spending a launch seat on it is not, because the
  -- ceiling exists to bound strangers.
  --
  -- ``subject IS NULL`` is the same rule reef_person_bind enforces, and it
  -- is load-bearing rather than tidy: without it, anybody able to prove
  -- control of an address a member already signed in with would be handed
  -- that member's account.
  UPDATE persons SET subject = p_subject
   WHERE email = lower(p_email) AND subject IS NULL
  RETURNING id, email, display_name INTO bound_id, bound_email, bound_name;
  IF FOUND THEN
    -- Cast explicitly: the columns are varchar(255) and the signature says
    -- text, which RETURN QUERY compares strictly rather than coercing.
    RETURN QUERY SELECT bound_id, bound_email::text, bound_name::text;
    RETURN;
  END IF;

  -- Nothing was bound: either no such row, or one whose subject is already
  -- set. The second case must not fall through to the insert -- the unique
  -- index on email would refuse it anyway, but as a raw driver error rather
  -- than the refusal the caller is written to expect.
  IF EXISTS (SELECT 1 FROM persons WHERE email = lower(p_email)) THEN
    RETURN;
  END IF;

  SELECT count(*) INTO taken FROM persons WHERE joined_open_door;
  IF taken >= p_seats THEN
    RETURN;
  END IF;

  RETURN QUERY
  INSERT INTO persons (id, email, subject, display_name, joined_open_door,
                       created_at)
  VALUES (gen_random_uuid(), lower(p_email), p_subject, p_display_name, true,
          LOCALTIMESTAMP)
  RETURNING id, email::text, display_name::text;
END
""",
        returns=_PERSON_ROW,
        writes=("persons",),
        volatility="VOLATILE",
        language="plpgsql",
    )


def avatar_statements() -> list[str]:
    """Return DDL for the functions that show a co-member's face.

    Deliberately **not** called from :func:`enable_statements`, for the same
    reason as :func:`appearance_statements` but by a narrower route: three
    historical migrations (``2026-08-07T11:00:00``, ``2026-08-08T10:00:00``
    and ``2026-08-12T09:00:00``) call ``enable_statements`` to re-apply the
    policies of their day, and ``persons.avatar_mime``/``avatar_bytes`` only
    arrive in ``2026-08-13T09:30:00``. A ``LANGUAGE sql`` body is parsed and
    validated when the function is *created*, so naming those columns from
    inside ``enable_statements`` refuses at that point and kills the chain on
    every database built from scratch -- while production, long past those
    migrations, would never notice. The two callers name it explicitly: the
    migration that adds these functions, and the suite's schema fixture.

    Why functions at all: ``persons`` is self-only under RLS
    (:func:`identity_policy_statements`), so a co-member's row -- picture
    included -- is unreachable by an ordinary query no matter how the web
    layer asks. The rule that *should* hold is "the people who share a space
    with you may see your face", and it is expressed here, in SQL, against
    the armed principal, rather than in a handler that has to remember.

    Both functions demand the caller is a member of ``p_space`` **and** that
    the person asked about is one too. The second half is not redundant:
    without it a member of any space could name any person id and pull their
    picture, turning a space membership into a lookup oracle over every
    account. Non-membership returns no rows rather than an error, matching
    :func:`reef_roster` -- a stranger learns nothing, not even that the space
    exists.

    Byte length rather than the bytes for the roster: it is what mints a
    cache-busting URL and tells the UI whether to fall back to initials, and
    it keeps a roster response from carrying every member's picture inline.

    :returns: SQL statements to execute in order
    """
    target_is_member = (
        "p.id IN (SELECT person_id FROM memberships WHERE space_id = p_space)"
    )
    return [
        *_function_ddl(
            "reef_member_faces(p_space uuid)",
            "SELECT p.id, octet_length(p.avatar_bytes)::int FROM persons p "
            f"WHERE {target_is_member} AND p.avatar_bytes IS NOT NULL "
            f"AND {_CALLER_IS_MEMBER}",
            returns="TABLE(person_id uuid, avatar_len int)",
            reads=("persons", "memberships"),
        ),
        *_function_ddl(
            "reef_member_avatar(p_space uuid, p_person uuid)",
            "SELECT p.avatar_mime, p.avatar_bytes FROM persons p "
            f"WHERE p.id = p_person AND {target_is_member} "
            f"AND p.avatar_bytes IS NOT NULL AND {_CALLER_IS_MEMBER}",
            returns="TABLE(avatar_mime text, avatar_bytes bytea)",
            reads=("persons", "memberships"),
        ),
    ]


def drop_avatar_statements() -> list[str]:
    """Return DDL undoing :func:`avatar_statements`.

    :returns: SQL statements to execute in order
    """
    return [
        "DROP FUNCTION IF EXISTS reef_member_faces(uuid)",
        "DROP FUNCTION IF EXISTS reef_member_avatar(uuid, uuid)",
    ]


def drop_open_door_statements() -> list[str]:
    """Return DDL undoing :func:`open_door_statements`.

    :returns: SQL statements to execute in order
    """
    return [
        "DROP FUNCTION IF EXISTS reef_open_door_admit(text, text, text, int)",
    ]


JOB_LEASE_SECONDS = 300
"""How long a claimed job may sit unlocked before another claim may take it.

A worker holds a row lock on its job for as long as it is working, and the
claim skips locked rows, so a job in progress is never taken however long it
runs. The lease only covers a worker that died: between claiming and
locking, or mid-job, when its lock went with its connection."""

JOB_MAX_ATTEMPTS = 5
"""How many times a job is started before it is given up on."""

_JOB_PREDICATE = f"person_id = {PRINCIPAL}"

_CLAIM_JOB = f"""
BEGIN
    -- A job whose worker died on its last allowed attempt would otherwise
    -- stay 'running' forever: nothing may claim it again, and the worker
    -- that would have recorded the failure is gone.
    UPDATE jobs SET status = 'failed', error = 'LeaseExpired',
        locked_by = NULL, locked_at = NULL, finished_at = LOCALTIMESTAMP
    WHERE id IN (
        SELECT d.id FROM jobs d
        WHERE d.status = 'running'
          AND d.attempts >= {JOB_MAX_ATTEMPTS}
          AND d.locked_at < LOCALTIMESTAMP - interval '{JOB_LEASE_SECONDS} seconds'
        FOR UPDATE SKIP LOCKED
    );
    RETURN QUERY
    UPDATE jobs j SET status = 'running', attempts = j.attempts + 1,
        locked_by = left(worker, 255), locked_at = LOCALTIMESTAMP
    WHERE j.id = (
        SELECT c.id FROM jobs c
        WHERE (c.status = 'queued' AND c.run_after <= LOCALTIMESTAMP)
           OR (c.status = 'running' AND c.locked_at
               < LOCALTIMESTAMP - interval '{JOB_LEASE_SECONDS} seconds')
        ORDER BY c.run_after, c.created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING j.id, j.kind::text, j.person_id, j.subject_ids, j.attempts;
END
"""


def job_statements() -> list[str]:
    """Return idempotent DDL for the job queue's constraints, policy and claim.

    Not part of :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives: historical migrations call that,
    and they run before ``jobs`` exists.

    **The policy.** One ``FOR ALL`` policy on ownership. A job belongs to
    the person it is done for; they may queue it, read it and settle it, and
    nobody else may see that it exists. A job row is not content, but it
    says when a person was active and how much they wrote, which is private
    here for the same reason an engram's existence is.

    **The claim.** A worker has to find the next job without knowing whose
    it is, and under the policy above an unarmed connection sees no jobs at
    all. ``reef_claim_job`` is the one exception, and it is kept as small as
    an exception can be:

    - It hands out **one** job per call and marks it taken. It cannot list
      or count the queue.
    - It returns **ids only**: the job, its kind, its person and its subject
      ids. There is no text to return, because a job row holds none.
    - Everything after the claim -- the work itself, marking it done,
      recording a failure -- happens armed as the job's person, under the
      ordinary policy. The function grants no ability to act as anyone.

    What a caller of it *can* do is take jobs it does not finish, which
    delays them by one lease. That is a nuisance to the queue and discloses
    nothing. Restricting the function to a dedicated worker role would close
    it, at the price of one more role provisioned by hand.

    All time arithmetic uses ``LOCALTIMESTAMP`` so that every comparison is
    made by the database against values the database wrote. The columns are
    ``timestamp without time zone`` like the rest of the schema, and mixing
    in a clock from the application would compare two different "nows".

    :returns: SQL statements to execute in order
    """
    statuses = "'queued', 'running', 'done', 'failed'"
    return [
        "ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_known",
        (
            f"ALTER TABLE jobs ADD CONSTRAINT jobs_status_known "
            f"CHECK (status IN ({statuses}))"
        ),
        # What the claim scans: only rows that could be claimed.
        (
            "CREATE INDEX IF NOT EXISTS jobs_claimable ON jobs (run_after, created_at) "
            "WHERE status IN ('queued', 'running')"
        ),
        # The latest reading job decides whether to hold a file back.
        # Include id so equal creation times need no extra sort.
        (
            "CREATE INDEX IF NOT EXISTS jobs_person_kind_latest "
            "ON jobs (person_id, kind, created_at DESC, id DESC)"
        ),
        # At most one whole-person job of a kind waiting per person, so
        # "queue it again, to be sure" is free and cannot pile up.
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_queued_per_person_kind "
            "ON jobs (person_id, kind) "
            "WHERE status = 'queued' AND cardinality(subject_ids) = 0"
        ),
        "ALTER TABLE jobs ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE jobs FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS jobs_owner ON jobs",
        (
            f"CREATE POLICY jobs_owner ON jobs "
            f"USING ({_JOB_PREDICATE}) WITH CHECK ({_JOB_PREDICATE})"
        ),
        *_function_ddl(
            "reef_claim_job(worker text)",
            _CLAIM_JOB,
            returns=(
                "TABLE(job_id uuid, job_kind text, job_person_id uuid, "
                "job_subject_ids uuid[], job_attempts integer)"
            ),
            writes=("jobs",),
            volatility="VOLATILE",
            language="plpgsql",
        ),
    ]


_LINK_READ = "from_engram_id IN (SELECT id FROM engrams) AND to_engram_id IN (SELECT id FROM engrams)"
"""Both ends readable. The subqueries run under ``engrams``' own policy, so
"in engrams" already means "in engrams this person may read"."""

_LINK_WRITE = (
    "from_engram_id IN (SELECT id FROM engrams "
    "WHERE space_id IN (SELECT reef_member_space_ids())) "
    "AND to_engram_id IN (SELECT id FROM engrams) "
    f"AND author_id = {PRINCIPAL} "
    # Never from a shared space into a personal one. The reader rule above
    # would hide such a link from the other members anyway; this keeps it
    # from being stored at all, so no later mistake can reveal it.
    "AND (NOT EXISTS (SELECT 1 FROM engrams t JOIN spaces c ON c.id = t.space_id "
    "WHERE t.id = links.to_engram_id AND c.kind = 'personal') "
    "OR EXISTS (SELECT 1 FROM engrams f JOIN spaces c ON c.id = f.space_id "
    "WHERE f.id = links.from_engram_id AND c.kind = 'personal'))"
)

_RETIRE_LINKS = f"""
    UPDATE links l SET status = 'superseded', superseded_at = LOCALTIMESTAMP
    FROM engrams p
    WHERE p.id = p_engram
      AND l.from_engram_id = p.id
      AND p.space_id IN (
          SELECT space_id FROM memberships
          WHERE person_id = {PRINCIPAL} AND role = 'member'
      )
      AND l.source = 'wikilink'
      AND l.status = 'active'
      AND NOT (l.target_text = ANY (COALESCE(p.reference_targets, '{{}}')))
"""


def link_statements() -> list[str]:
    """Return idempotent DDL for the link table's constraints and policies.

    Not part of :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives.

    **Reading.** A link is visible when both of its engrams are. For anyone
    else it does not exist, which is what keeps a backlink or a count from
    saying that an engram they cannot read is there.

    **Writing.** A person may add a link from an engram they may change to an
    engram they may read, in their own name. That is all a person may do to
    this table directly: there is no ``UPDATE`` policy and no ``DELETE``
    policy, so both are refused outright.

    **Retiring.** A link has to end when its words leave the engram, whoever
    is saving. But the person saving may be unable to *see* the link: its
    other end can sit in a space they are not in. An ``UPDATE`` policy
    cannot help them without showing them the row, and the row proves the
    other engram exists. So retiring is a function, ``reef_retire_links``:

    - It takes an engram id and nothing else. What to retire is decided from
      the engram's own stored targets, never from anything the caller says.
    - It does nothing unless the caller may change that engram.
    - It returns nothing, so it cannot be used to count or probe.

    Rows are never deleted except with one of their engrams.

    :returns: SQL statements to execute in order
    """
    kinds = "'references', 'supersedes', 'contradicts', 'derived_from', "
    kinds += "'about', 'part_of'"
    checks = {
        "links_kind_known": f"kind IN ({kinds})",
        "links_source_known": "source IN ('author', 'wikilink', 'dream', 'import')",
        "links_status_known": (
            "status IN ('active', 'proposed', 'rejected', 'superseded')"
        ),
        "links_not_to_itself": "from_engram_id <> to_engram_id",
    }
    statements: list[str] = []
    for name, check in checks.items():
        statements.append(f"ALTER TABLE links DROP CONSTRAINT IF EXISTS {name}")
        statements.append(f"ALTER TABLE links ADD CONSTRAINT {name} CHECK ({check})")
    statements += [
        # One live link of a kind between two engrams.
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS links_one_active "
            "ON links (from_engram_id, to_engram_id, kind) WHERE status = 'active'"
        ),
        # Backlinks: "what points here".
        (
            "CREATE INDEX IF NOT EXISTS links_inbound "
            "ON links (to_engram_id) WHERE status = 'active'"
        ),
        "ALTER TABLE links ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE links FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS links_select ON links",
        "DROP POLICY IF EXISTS links_insert ON links",
        f"CREATE POLICY links_select ON links FOR SELECT USING ({_LINK_READ})",
        f"CREATE POLICY links_insert ON links FOR INSERT WITH CHECK ({_LINK_WRITE})",
        *_function_ddl(
            "reef_retire_links(p_engram uuid)",
            _RETIRE_LINKS,
            returns="void",
            reads=("engrams", "memberships"),
            writes=("links",),
            volatility="VOLATILE",
        ),
    ]
    return statements


_USAGE_PREDICATE = f"person_id = {PRINCIPAL} AND engram_id IN (SELECT id FROM engrams)"
"""Own rows, about engrams the person can still read. The second half matters
after someone leaves a space: their old usage rows would otherwise go on
naming engrams they may no longer see."""


def usage_statements() -> list[str]:
    """Return idempotent DDL arming RLS on the usage log and activation.

    Not part of :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives.

    Both tables are private to one person. Nothing looks across people, so
    there is no helper function here at all.

    ``engram_access`` is a log: a person may add to it, read it and delete
    from it, but there is no ``UPDATE`` policy, so a row cannot be
    rewritten. Deleting is allowed because a person may remove what Reef
    recorded about them.

    **A known gap.** Rows about an engram the person can no longer read are
    hidden from them, and a row that cannot be seen cannot be deleted
    either. So after leaving a space, a person's usage of its engrams stays
    stored, invisible to everyone, until the engram or their account is
    deleted. Showing them those rows instead would let a former member
    watch which of a space's engrams still exist. The proper fix is to remove
    the rows when the membership ends.

    ``engram_heat`` is derived from that log and is replaced whenever it is
    recomputed, so its one policy covers every command.

    :returns: SQL statements to execute in order
    """
    return [
        "ALTER TABLE engram_access DROP CONSTRAINT IF EXISTS engram_access_via_known",
        (
            "ALTER TABLE engram_access ADD CONSTRAINT engram_access_via_known "
            "CHECK (via IN ('read', 'write', 'remember', 'web'))"
        ),
        "ALTER TABLE engram_access DROP CONSTRAINT IF EXISTS engram_access_weight_sane",
        (
            "ALTER TABLE engram_access ADD CONSTRAINT engram_access_weight_sane "
            "CHECK (weight > 0 AND weight <= 10)"
        ),
        # What recomputing activation reads: one person's log, newest first
        # per engram.
        (
            "CREATE INDEX IF NOT EXISTS engram_access_recent "
            "ON engram_access (person_id, engram_id, accessed_at DESC)"
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS engram_heat_person_engram "
            "ON engram_heat (person_id, engram_id)"
        ),
        "ALTER TABLE engram_access ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE engram_access FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS engram_access_select ON engram_access",
        "DROP POLICY IF EXISTS engram_access_insert ON engram_access",
        "DROP POLICY IF EXISTS engram_access_delete ON engram_access",
        (
            f"CREATE POLICY engram_access_select ON engram_access FOR SELECT "
            f"USING ({_USAGE_PREDICATE})"
        ),
        (
            f"CREATE POLICY engram_access_insert ON engram_access FOR INSERT "
            f"WITH CHECK ({_USAGE_PREDICATE})"
        ),
        (
            f"CREATE POLICY engram_access_delete ON engram_access FOR DELETE "
            f"USING (person_id = {PRINCIPAL})"
        ),
        "ALTER TABLE engram_heat ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE engram_heat FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS engram_heat_owner ON engram_heat",
        (
            f"CREATE POLICY engram_heat_owner ON engram_heat "
            f"USING ({_USAGE_PREDICATE}) WITH CHECK ({_USAGE_PREDICATE})"
        ),
    ]


SEARCH_LOG_DAYS = 90
"""How long a logged search is kept. Fixed here, in the purge function's own
body, so no caller can ask for a shorter time and delete other people's rows
early."""

SEARCH_LOG_MAX_QUERY = 500
"""The longest query text stored. A longer one is cut by the application."""

#: Only numbers leave the search totals function, never a query or an identity.
SEARCH_LOG_TOTALS_COLUMNS = ("searches", "misses")

SEARCH_LOG_TOTALS_MIN_DAYS = 7
"""The fewest whole days the search totals cover, as for the call log."""

SEARCH_LOG_TOTALS_MAX_DAYS = SEARCH_LOG_DAYS - 1
"""Leave the oldest retention day out: its rows expire during the day."""

#: Both logs keep UTC clock time as a ``timestamp``. Comparing timestamp
#: bounds directly means a caller's ``TimeZone`` cannot move the window.
_TODAY_UTC = "date_trunc('day', now() AT TIME ZONE 'UTC')"

_SEARCH_LOG_TOTALS = f"""
BEGIN
    IF p_days IS NULL
       OR p_days NOT BETWEEN {SEARCH_LOG_TOTALS_MIN_DAYS} AND {SEARCH_LOG_TOTALS_MAX_DAYS} THEN
        RAISE EXCEPTION 'search log totals take {SEARCH_LOG_TOTALS_MIN_DAYS} to {SEARCH_LOG_TOTALS_MAX_DAYS} days'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT count(*), count(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM engram_access a
        WHERE a.person_id = s.person_id AND a.session_id = s.session_id
          AND a.via = 'read' AND a.accessed_at >= s.searched_at
          AND a.accessed_at < s.searched_at + interval '1 hour'
          AND a.accessed_at < {_TODAY_UTC}
          AND NOT (a.engram_id = ANY (s.engram_ids))
    ))
    FROM search_log s
    WHERE s.searched_at >= {_TODAY_UTC} - make_interval(days => p_days)
      AND s.searched_at < {_TODAY_UTC}
      AND NOT EXISTS (SELECT 1 FROM test_accounts t WHERE t.person_id = s.person_id);
END
"""

_PURGE_SEARCH_LOG = f"""
DECLARE
    removed integer;
BEGIN
    DELETE FROM search_log
    WHERE searched_at < (now() AT TIME ZONE 'UTC') - make_interval(days => {SEARCH_LOG_DAYS});
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
"""


def search_log_statements() -> list[str]:
    """Return idempotent DDL for the search log, its purge and its totals.

    Not part of :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives.

    **The policies.** The table is private to one person, like the usage
    log. A person may add a row, read their rows and delete them. There is
    no ``UPDATE`` policy, so a row cannot be rewritten. Nobody reads another
    person's rows.

    **The purge.** Rows must go after :data:`SEARCH_LOG_DAYS` days, also for
    a person who never comes back, and no armed connection can delete
    another person's rows. ``reef_purge_search_log`` is the one exception,
    kept as small as it can be:

    - It takes **no argument**. The age is fixed in its body, so a caller
      cannot delete anything younger.
    - It returns **a count**, never a row. It cannot read a query.

    What a caller of it can do is delete rows that were due for deletion
    anyway.

    **The totals.** ``reef_search_log_totals(p_days)`` counts searches and
    searches with at least one missed read, across people. Its owner reads
    both ``search_log`` and ``engram_access``, but returns only those two
    numbers. The match uses the same rule as :func:`reef.searchlog.misses`,
    with an exception for reads in a shared space the person has since left:
    row security hides those reads from their miss list, but the totals
    still count them. The parity test in ``tests/test_search_log_totals.py``
    states this exception.
    Multiple missed reads count a search once. Both searches and matching
    reads must be before the start of today. A search in the last hour of
    a day may therefore be counted as not missed: this is the price of
    whole days, so today's reads cannot change yesterday's count.
    It accepts 7 to 89 whole UTC days ending at the start of today, one day
    less than retention so the hourly purge cannot shrink the oldest day.
    Like the call totals, it skips flagged test accounts and grants no access
    to another person's rows. It needs ``test_accounts`` to exist.

    :returns: SQL statements to execute in order
    """
    own = f"person_id = {PRINCIPAL}"
    return [
        "ALTER TABLE search_log DROP CONSTRAINT IF EXISTS search_log_query_sane",
        (
            f"ALTER TABLE search_log ADD CONSTRAINT search_log_query_sane "
            f"CHECK (char_length(query) BETWEEN 1 AND {SEARCH_LOG_MAX_QUERY})"
        ),
        "ALTER TABLE search_log DROP CONSTRAINT IF EXISTS search_log_results_sane",
        (
            "ALTER TABLE search_log ADD CONSTRAINT search_log_results_sane "
            "CHECK (cardinality(engram_ids) <= 50)"
        ),
        # What a person's own reading of their log uses.
        (
            "CREATE INDEX IF NOT EXISTS search_log_recent "
            "ON search_log (person_id, searched_at DESC)"
        ),
        # What the purge scans.
        "CREATE INDEX IF NOT EXISTS search_log_age ON search_log (searched_at)",
        "ALTER TABLE search_log ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE search_log FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS search_log_select ON search_log",
        "DROP POLICY IF EXISTS search_log_insert ON search_log",
        "DROP POLICY IF EXISTS search_log_delete ON search_log",
        f"CREATE POLICY search_log_select ON search_log FOR SELECT USING ({own})",
        (
            f"CREATE POLICY search_log_insert ON search_log FOR INSERT "
            f"WITH CHECK ({own})"
        ),
        f"CREATE POLICY search_log_delete ON search_log FOR DELETE USING ({own})",
        *_function_ddl(
            "reef_purge_search_log()",
            _PURGE_SEARCH_LOG,
            returns="integer",
            writes=("search_log",),
            volatility="VOLATILE",
            language="plpgsql",
        ),
        *_function_ddl(
            "reef_search_log_totals(p_days integer)",
            _SEARCH_LOG_TOTALS,
            returns="TABLE(searches bigint, misses bigint)",
            reads=("search_log", "engram_access", "test_accounts"),
            language="plpgsql",
        ),
    ]


CALL_LOG_DAYS = 90
"""How many days a logged call is kept. A constant, not a setting: the privacy
page promises it."""

CALL_LOG_MAX_REASON = 200
"""The longest reason stored. A longer one is cut by the application."""

WRITER_KINDS = ("assistant", "person", "system")
"""Who made a call or a change: an assistant over MCP, a person on the website,
or the system. ``system`` is reserved and unused."""

_PURGE_CALL_LOG = f"""
DECLARE
    removed integer;
BEGIN
    DELETE FROM call_log
    WHERE called_at < (now() AT TIME ZONE 'UTC') - make_interval(days => {CALL_LOG_DAYS});
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
"""

#: The columns :func:`call_log_statements`' totals return, and nothing else.
#: Not how many people: in a reef of three, that would point at one of them.
CALL_LOG_TOTALS_COLUMNS = ("method", "outcome", "calls", "empty_reasons")

CALL_LOG_TOTALS_MIN_DAYS = 7
"""The fewest whole days the call log's totals cover. A shorter window could
be aimed at one call."""

CALL_LOG_TOTALS_MAX_DAYS = CALL_LOG_DAYS - 1
"""Leave the oldest retention day out: its rows expire during the day."""

_CALL_LOG_TOTALS = f"""
BEGIN
    IF p_days IS NULL
       OR p_days NOT BETWEEN {CALL_LOG_TOTALS_MIN_DAYS} AND {CALL_LOG_TOTALS_MAX_DAYS} THEN
        RAISE EXCEPTION 'call log totals take {CALL_LOG_TOTALS_MIN_DAYS} to {CALL_LOG_TOTALS_MAX_DAYS} days'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    RETURN QUERY
    SELECT c.method::text, c.outcome::text, count(*),
           count(*) FILTER (WHERE c.reason = '')
    FROM call_log c
    WHERE c.called_at >= {_TODAY_UTC} - make_interval(days => p_days)
      AND c.called_at < {_TODAY_UTC}
      AND NOT EXISTS (SELECT 1 FROM test_accounts t WHERE t.person_id = c.person_id)
    GROUP BY c.method, c.outcome
    ORDER BY c.method, c.outcome;
END
"""


def call_log_statements() -> list[str]:
    """Return idempotent DDL arming RLS on the call log, its purge and its totals.

    Not part of :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives. Also adds the check on
    ``revisions.channel``, the writer kind kept in engram history.

    **The policies.** The table is private to one person. A person may add a
    row for themselves and read their own rows. There is no ``UPDATE`` or
    ``DELETE`` policy: a row is never rewritten and leaves only by the purge
    or by the person's account being deleted. Nobody reads another person's
    rows, operators included.

    **The purge.** ``reef_purge_call_log`` is built like
    ``reef_purge_search_log``: no argument, returns a count and never a row,
    and deletes only rows that were due for deletion anyway.

    **The totals.** ``reef_call_log_totals(p_days)`` is the one way to look
    across people, and it returns only totals: for each method and outcome,
    how many calls and how many had no reason. No person, reason, session or
    time leaves it, and not how many people either. It counts whole days in
    UTC, from the start of the day ``p_days`` days ago to the start of today,
    and refuses fewer than :data:`CALL_LOG_TOTALS_MIN_DAYS` days or more than
    :data:`CALL_LOG_TOTALS_MAX_DAYS`, one day less than retention so the
    hourly purge cannot shrink the oldest day. So no window can be aimed
    at one moment. This is what the call log is for:
    learning which tools assistants use and what they ask for that no tool
    serves, without reading anybody's calls. Statistics across people skip
    flagged test accounts (see :func:`test_account_statements`), so scripted
    conversations do not colour them. :func:`call_log_statements` therefore
    needs ``test_accounts`` to exist.

    :returns: SQL statements to execute in order
    """
    own = f"person_id = {PRINCIPAL}"
    writers = ", ".join(f"'{kind}'" for kind in WRITER_KINDS)
    return [
        "ALTER TABLE call_log DROP CONSTRAINT IF EXISTS call_log_reason_sane",
        (
            f"ALTER TABLE call_log ADD CONSTRAINT call_log_reason_sane "
            f"CHECK (char_length(reason) <= {CALL_LOG_MAX_REASON})"
        ),
        "ALTER TABLE call_log DROP CONSTRAINT IF EXISTS call_log_writer_known",
        (
            f"ALTER TABLE call_log ADD CONSTRAINT call_log_writer_known "
            f"CHECK (writer IN ({writers}))"
        ),
        "ALTER TABLE revisions DROP CONSTRAINT IF EXISTS revisions_channel_known",
        (
            f"ALTER TABLE revisions ADD CONSTRAINT revisions_channel_known "
            f"CHECK (channel IS NULL OR channel IN ({writers}))"
        ),
        # What a person's own reading of their log uses.
        (
            "CREATE INDEX IF NOT EXISTS call_log_recent "
            "ON call_log (person_id, called_at DESC)"
        ),
        # What the purge scans.
        "CREATE INDEX IF NOT EXISTS call_log_age ON call_log (called_at)",
        "ALTER TABLE call_log ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE call_log FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS call_log_select ON call_log",
        "DROP POLICY IF EXISTS call_log_insert ON call_log",
        f"CREATE POLICY call_log_select ON call_log FOR SELECT USING ({own})",
        f"CREATE POLICY call_log_insert ON call_log FOR INSERT WITH CHECK ({own})",
        *_function_ddl(
            "reef_purge_call_log()",
            _PURGE_CALL_LOG,
            returns="integer",
            writes=("call_log",),
            volatility="VOLATILE",
            language="plpgsql",
        ),
        *_function_ddl(
            "reef_call_log_totals(p_days integer)",
            _CALL_LOG_TOTALS,
            returns="TABLE(method text, outcome text, calls bigint, empty_reasons bigint)",
            reads=("call_log", "test_accounts"),
            language="plpgsql",
        ),
    ]


_TEST_ACCOUNT_RESET = f"""
DECLARE
    me uuid := {PRINCIPAL};
    stamp timestamptz;
BEGIN
    IF me IS NULL OR NOT EXISTS (SELECT 1 FROM test_accounts WHERE person_id = me) THEN
        RAISE EXCEPTION 'not a test account'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF EXISTS (
        SELECT 1 FROM memberships m JOIN spaces s ON s.id = m.space_id
        WHERE m.person_id = me AND s.owner_person_id <> me
    ) OR EXISTS (
        SELECT 1 FROM memberships o JOIN spaces s ON s.id = o.space_id
        WHERE o.person_id <> me AND (s.owner_person_id = me
            OR s.id IN (SELECT space_id FROM memberships WHERE person_id = me))
    ) THEN
        RAISE EXCEPTION 'shares a space with others'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    DELETE FROM jobs WHERE person_id = me AND status = 'queued';
    DELETE FROM embedding_spend WHERE person_id = me;
    DELETE FROM reading_spend WHERE person_id = me;
    UPDATE test_accounts SET reset_at = now() WHERE person_id = me
    RETURNING reset_at INTO stamp;
    RETURN stamp;
END
"""

_TEST_ACCOUNT_HURRY = f"""
DECLARE
    me uuid := {PRINCIPAL};
    moved integer;
BEGIN
    IF me IS NULL OR NOT EXISTS (SELECT 1 FROM test_accounts WHERE person_id = me) THEN
        RETURN 0;
    END IF;
    UPDATE jobs SET run_after = LOCALTIMESTAMP WHERE person_id = me AND status = 'queued';
    GET DIAGNOSTICS moved = ROW_COUNT;
    RETURN moved;
END
"""


def test_account_statements() -> list[str]:
    """Return idempotent DDL for test accounts: the flag and its two functions.

    Not part of :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives.

    **The flag.** ``test_accounts`` holds one row per made-up account. A
    person may read their own row and nothing else. There is no ``INSERT``,
    ``UPDATE`` or ``DELETE`` policy, so the application cannot flag anyone,
    not even itself: the operator does, past row security.

    **The functions.** Both act for the armed person only, so nobody can
    reset or hurry another account.

    * ``reef_test_account_reset()`` raises ``not a test account`` (SQLSTATE
      ``insufficient_privilege``) for an unflagged caller, and ``shares a
      space with others`` (same SQLSTATE) for a caller who is a member of a
      space someone else owns, or who owns or is in a space with another
      member. It checks past row security, so a membership hidden from the
      caller still counts. Otherwise it deletes the caller's queued jobs and
      their embedding and reading spend, stamps ``reset_at`` and returns it.
      :func:`reef.testaccount.reset` calls it before deleting anything, in
      the same transaction.
    * ``reef_test_account_hurry()`` makes the caller's queued jobs due now, on
      the clock the worker claims by, and returns how many. It returns 0 for
      an unflagged caller.

    :returns: SQL statements to execute in order
    """
    own = f"person_id = {PRINCIPAL}"
    roles = [
        f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        f"'{role}') THEN EXECUTE 'GRANT SELECT ON test_accounts TO {role}'; "
        f"END IF; END $do$"
        for role in _EXECUTOR_ROLES
    ]
    return [
        "ALTER TABLE test_accounts ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE test_accounts FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS test_accounts_select ON test_accounts",
        f"CREATE POLICY test_accounts_select ON test_accounts FOR SELECT USING ({own})",
        *roles,
        *_function_ddl(
            "reef_test_account_reset()",
            _TEST_ACCOUNT_RESET,
            returns="timestamptz",
            reads=("test_accounts", "memberships", "spaces"),
            writes=("jobs", "embedding_spend", "reading_spend"),
            volatility="VOLATILE",
            language="plpgsql",
        ),
        _for_authz("GRANT UPDATE (reset_at) ON test_accounts TO %I"),
        *_function_ddl(
            "reef_test_account_hurry()",
            _TEST_ACCOUNT_HURRY,
            returns="integer",
            reads=("test_accounts",),
            writes=("jobs",),
            volatility="VOLATILE",
            language="plpgsql",
        ),
    ]


_EMBEDDING_READ = "engram_id IN (SELECT id FROM engrams)"
"""The engram is readable. The subquery runs under ``engrams``' own policy."""

_EMBEDDING_WRITE = "engram_id IN (SELECT id FROM engrams WHERE space_id IN (SELECT reef_member_space_ids()))"
"""The engram is in a space the person may write to. A viewer may search a
space's vectors but not replace them: a planted vector would steer every
other member's search."""


def embedding_statements() -> list[str]:
    """Return idempotent DDL for engram embeddings and the daily spend.

    Not part of :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives. Needs the ``vector`` extension,
    which a superuser provisions once: see ``scripts/provision_pgvector.sql``.

    **``engram_embeddings``.** A vector is visible exactly when its engram is.
    A vector is not harmless derived data: text can be recovered from an
    embedding well enough to matter, so it gets the engram's own protection
    and no less. Writing needs write access to the engram's space.

    There is **no vector index**. A search reads one person's rows and
    compares them all, which is exact and takes milliseconds at the sizes
    reef has. An index also needs one fixed vector length, and this table
    holds whatever length its model has. Add one, for one model, when a
    space outgrows the exact scan.

    **``embedding_spend``.** Private to the person. One row per person and
    day.

    :returns: SQL statements to execute in order
    """
    own = f"person_id = {PRINCIPAL}"
    return [
        "ALTER TABLE engram_embeddings DROP CONSTRAINT IF EXISTS engram_embeddings_dims",
        (
            "ALTER TABLE engram_embeddings ADD CONSTRAINT engram_embeddings_dims "
            "CHECK (dims = vector_dims(embedding) AND dims BETWEEN 1 AND 4096)"
        ),
        # One vector per piece and model. COALESCE because NULL, the whole
        # engram, must count as one value and not as "never equal".
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS engram_embeddings_piece "
            "ON engram_embeddings (engram_id, model_id, COALESCE(section_anchor, ''))"
        ),
        "ALTER TABLE engram_embeddings ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE engram_embeddings FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS engram_embeddings_select ON engram_embeddings",
        "DROP POLICY IF EXISTS engram_embeddings_insert ON engram_embeddings",
        "DROP POLICY IF EXISTS engram_embeddings_delete ON engram_embeddings",
        (
            f"CREATE POLICY engram_embeddings_select ON engram_embeddings "
            f"FOR SELECT USING ({_EMBEDDING_READ})"
        ),
        (
            f"CREATE POLICY engram_embeddings_insert ON engram_embeddings "
            f"FOR INSERT WITH CHECK ({_EMBEDDING_WRITE})"
        ),
        (
            f"CREATE POLICY engram_embeddings_delete ON engram_embeddings "
            f"FOR DELETE USING ({_EMBEDDING_WRITE})"
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS embedding_spend_person_day "
            "ON embedding_spend (person_id, day)"
        ),
        "ALTER TABLE embedding_spend DROP CONSTRAINT IF EXISTS embedding_spend_sane",
        (
            "ALTER TABLE embedding_spend ADD CONSTRAINT embedding_spend_sane "
            "CHECK (pieces >= 0)"
        ),
        "ALTER TABLE embedding_spend ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE embedding_spend FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS embedding_spend_owner ON embedding_spend",
        (
            f"CREATE POLICY embedding_spend_owner ON embedding_spend "
            f"USING ({own}) WITH CHECK ({own})"
        ),
    ]


READERS = ("pdf-text", "tesseract", "markdown", "plain", "html", "docx", "xlsx", "pptx")
"""What may read a page: a PDF's own text layer, Tesseract on a scan, or one
reader per text format. Every one runs on Reef's own servers. Adding one
changes ``file_pages_reader_known``, which takes a migration."""

_TEXT_STATUSES = ("waiting", "read", "partly_read", "unreadable", "not_read")
"""The values of :class:`reef.models.TextStatus`. Null means never looked at."""

_FILE_READ = "attachment_id IN (SELECT id FROM attachments)"
"""The file is readable. The subquery runs under ``attachments``' own policy."""

_FILE_WRITE = "attachment_id IN (SELECT id FROM attachments WHERE space_id IN (SELECT reef_member_space_ids()))"
"""The file is in a space the person may write to. A viewer may read a file's
text and search its vectors, but not change them: planted text or a planted
vector would steer every other member's search."""


def file_text_statements() -> list[str]:
    """Return idempotent DDL for the text read from files, and what it costs.

    Not part of :func:`enable_statements`, for the reason
    :func:`appearance_statements` gives. Needs the ``vector`` extension, as
    :func:`embedding_statements` does.

    **``file_pages``.** One row per page, numbered from 1. A page is visible
    exactly when its file is: the text inside a file says at least as much
    as the file's description. Writing needs write access to the file's
    space. A page may be rewritten in place, so a better reading can replace
    a worse one.

    **``file_embeddings``.** Built and guarded as ``engram_embeddings`` is,
    with the file in place of the engram. No ``UPDATE`` policy: a vector is
    replaced by deleting it and adding the new one. No vector index, for the
    reason :func:`embedding_statements` gives.

    **``reading_spend``.** Private to the person, as ``embedding_spend`` is.
    One row per person, day and reader.

    **``attachments.text_status``.** Only the values of
    :class:`reef.models.TextStatus`, or null for a file never looked at.

    :returns: SQL statements to execute in order
    """
    own = f"person_id = {PRINCIPAL}"
    readers = ", ".join(f"'{reader}'" for reader in READERS)
    statuses = ", ".join(f"'{status}'" for status in _TEXT_STATUSES)
    return [
        "ALTER TABLE attachments DROP CONSTRAINT IF EXISTS attachments_text_status_known",
        (
            "ALTER TABLE attachments ADD CONSTRAINT attachments_text_status_known "
            f"CHECK (text_status IS NULL OR text_status IN ({statuses}))"
        ),
        "ALTER TABLE file_pages DROP CONSTRAINT IF EXISTS file_pages_reader_known",
        (
            "ALTER TABLE file_pages ADD CONSTRAINT file_pages_reader_known "
            f"CHECK (reader IN ({readers}))"
        ),
        "ALTER TABLE file_pages DROP CONSTRAINT IF EXISTS file_pages_sane",
        (
            "ALTER TABLE file_pages ADD CONSTRAINT file_pages_sane "
            "CHECK (number >= 1 AND (confidence IS NULL OR confidence BETWEEN 0 AND 100))"
        ),
        # One text per page. It also serves the cascade from attachments.
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS file_pages_number "
            "ON file_pages (attachment_id, number)"
        ),
        "ALTER TABLE file_pages ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE file_pages FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS file_pages_select ON file_pages",
        "DROP POLICY IF EXISTS file_pages_insert ON file_pages",
        "DROP POLICY IF EXISTS file_pages_update ON file_pages",
        "DROP POLICY IF EXISTS file_pages_delete ON file_pages",
        f"CREATE POLICY file_pages_select ON file_pages FOR SELECT USING ({_FILE_READ})",
        (
            f"CREATE POLICY file_pages_insert ON file_pages "
            f"FOR INSERT WITH CHECK ({_FILE_WRITE})"
        ),
        (
            f"CREATE POLICY file_pages_update ON file_pages "
            f"FOR UPDATE USING ({_FILE_WRITE}) WITH CHECK ({_FILE_WRITE})"
        ),
        f"CREATE POLICY file_pages_delete ON file_pages FOR DELETE USING ({_FILE_WRITE})",
        "ALTER TABLE file_embeddings DROP CONSTRAINT IF EXISTS file_embeddings_dims",
        (
            "ALTER TABLE file_embeddings ADD CONSTRAINT file_embeddings_dims "
            "CHECK (dims = vector_dims(embedding) AND dims BETWEEN 1 AND 4096)"
        ),
        # One vector per chunk and model. COALESCE because NULL, the whole
        # file, must count as one value and not as "never equal".
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS file_embeddings_chunk "
            "ON file_embeddings (attachment_id, model_id, COALESCE(chunk_anchor, ''))"
        ),
        "ALTER TABLE file_embeddings ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE file_embeddings FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS file_embeddings_select ON file_embeddings",
        "DROP POLICY IF EXISTS file_embeddings_insert ON file_embeddings",
        "DROP POLICY IF EXISTS file_embeddings_delete ON file_embeddings",
        (
            f"CREATE POLICY file_embeddings_select ON file_embeddings "
            f"FOR SELECT USING ({_FILE_READ})"
        ),
        (
            f"CREATE POLICY file_embeddings_insert ON file_embeddings "
            f"FOR INSERT WITH CHECK ({_FILE_WRITE})"
        ),
        (
            f"CREATE POLICY file_embeddings_delete ON file_embeddings "
            f"FOR DELETE USING ({_FILE_WRITE})"
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS reading_spend_person_day_reader "
            "ON reading_spend (person_id, day, reader)"
        ),
        "ALTER TABLE reading_spend DROP CONSTRAINT IF EXISTS reading_spend_sane",
        (
            "ALTER TABLE reading_spend ADD CONSTRAINT reading_spend_sane "
            "CHECK (pages >= 0 AND input_tokens >= 0 AND output_tokens >= 0)"
        ),
        "ALTER TABLE reading_spend ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE reading_spend FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS reading_spend_owner ON reading_spend",
        (
            f"CREATE POLICY reading_spend_owner ON reading_spend "
            f"USING ({own}) WITH CHECK ({own})"
        ),
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

    Covers ``engrams`` and ``attachments`` (membership via their own
    ``space_id``) and ``revisions`` (membership via their engram's space), each
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
