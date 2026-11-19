"""The security and search DDL as it stood on 19 November 2026, frozen.

Historical migrations call these, never :mod:`reef.rls` or
:mod:`reef.search`. On that day a space was called a cove and an engram a
page, in the tables, columns, policies and helper functions alike, and every
migration before ``2026-11-20T10:00:00`` runs against a schema with those
names. Today's DDL names the renamed objects, which do not exist yet at that
point in the chain, so a database built from scratch -- a fresh deploy, or
the restore drill in ``docs/restore.md`` -- would fail at the first of them.

``2026-11-20T10:00:00`` renames the schema and then applies the DDL of its
own day, carried as fixed text, so production and the test suite end up with
the same policies. ``tests/test_migration_chain.py`` checks that.

Each function is a copy of the one of the same name, with its long
explanation left behind: the reasoning lives on in :mod:`reef.rls` and in
git history. Never edit this module. A change here changes what history
did, and only on databases built from scratch, where nobody is looking.
"""

PRINCIPAL = "NULLIF(current_setting('app.person_id', true), '')::uuid"


AUTHZ_ROLE = "reef_authz"


FORMER_AUTHZ_ROLE = "rif_authz"


def authz_role_expression() -> str:
    """Frozen copy of ``reef.rls.authz_role_expression``.

    :returns: a SQL scalar expression returning ``text``
    """
    return (
        "COALESCE("
        f"(SELECT rolname FROM pg_roles WHERE rolname = '{AUTHZ_ROLE}'), "
        f"(SELECT rolname FROM pg_roles WHERE rolname = '{FORMER_AUTHZ_ROLE}'), "
        f"'{AUTHZ_ROLE}')"
    )


def _for_authz(template: str) -> str:
    """Frozen copy of ``reef.rls._for_authz``.

    :param template: a ``format()`` template with a single ``%I``
    :returns: a DO block that resolves the role and executes the statement
    """
    literal = template.replace("'", "''")
    return (
        f"DO $authz$ DECLARE authz text := {authz_role_expression()}; "
        f"BEGIN EXECUTE format('{literal}', authz); END $authz$"
    )


_EXECUTOR_ROLES = (
    "reef_app",
    "reef",
    "reef_probe",
    "rif_app",
    "rif",
    "rif_probe",
)


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
    """Frozen copy of ``reef.rls._function_ddl``.

    :param name: the function signature, e.g. ``reef_cove_ids()``
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
            f"SET search_path = public, pg_catalog AS $reef${body}$reef$"
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


def create_authz_role_statements() -> list[str]:
    """Frozen copy of ``reef.rls.create_authz_role_statements``.

    :returns: SQL statements to execute in order
    """
    return [
        f"CREATE ROLE {AUTHZ_ROLE} NOLOGIN BYPASSRLS",
        f"GRANT {AUTHZ_ROLE} TO CURRENT_USER",
        f"GRANT CREATE ON SCHEMA public TO {AUTHZ_ROLE}",
    ]


def authz_statements() -> list[str]:
    """Frozen copy of ``reef.rls.authz_statements``.

    :returns: SQL statements to execute in order
    """
    return _function_ddl(
        "reef_cove_ids()",
        f"SELECT cove_id FROM memberships WHERE person_id = {PRINCIPAL}",
        reads=("memberships",),
    ) + _function_ddl(
        "reef_member_cove_ids()",
        f"SELECT cove_id FROM memberships WHERE person_id = {PRINCIPAL} "
        f"AND role = 'member'",
        reads=("memberships",),
    )


_CALLER_IS_MEMBER = (
    f"p_cove IN (SELECT cove_id FROM memberships WHERE person_id = {PRINCIPAL})"
)


_CALLER_IS_OWNER = (
    "EXISTS (SELECT 1 FROM coves s WHERE s.id = p_cove "
    f"AND s.owner_person_id = {PRINCIPAL})"
)


def disclosure_statements() -> list[str]:
    """Frozen copy of ``reef.rls.disclosure_statements``.

    :returns: SQL statements to execute in order
    """
    return [
        *_function_ddl(
            "reef_roster(p_cove uuid)",
            "SELECT p.id, p.display_name, "
            f"CASE WHEN {_CALLER_IS_OWNER} THEN p.email ELSE '' END "
            "FROM persons p WHERE p.id IN "
            "(SELECT person_id FROM memberships WHERE cove_id = p_cove) "
            f"AND {_CALLER_IS_MEMBER} ORDER BY p.display_name",
            returns="TABLE(person_id uuid, member_name text, member_email text)",
            reads=("persons", "memberships", "coves"),
        ),
        *_function_ddl(
            "reef_cove_owner(p_cove uuid)",
            "SELECT p.display_name, p.email FROM persons p JOIN coves s "
            "ON s.owner_person_id = p.id WHERE s.id = p_cove "
            f"AND {_CALLER_IS_MEMBER}",
            returns="TABLE(owner_name text, owner_email text)",
            reads=("persons", "coves", "memberships"),
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


_IDENTITY_TABLES = ("persons", "coves", "memberships")


def identity_policy_statements() -> list[str]:
    """Frozen copy of ``reef.rls.identity_policy_statements``.

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
            "CREATE POLICY coves_member_select ON coves FOR SELECT "
            "USING (id IN (SELECT reef_cove_ids()))"
        ),
        (
            f"CREATE POLICY coves_owner_select ON coves FOR SELECT "
            f"USING ({owner_is_principal})"
        ),
        (
            f"CREATE POLICY coves_owner_insert ON coves FOR INSERT "
            f"WITH CHECK ({owner_is_principal})"
        ),
        (
            "CREATE POLICY coves_member_update ON coves FOR UPDATE "
            "USING (id IN (SELECT reef_member_cove_ids())) "
            "WITH CHECK (id IN (SELECT reef_member_cove_ids()))"
        ),
        (
            f"CREATE POLICY coves_owner_delete ON coves FOR DELETE "
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
            "USING (cove_id IN (SELECT reef_cove_ids()))"
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
            "WITH CHECK (reef_owns_cove(cove_id))"
        ),
        (
            f"CREATE POLICY memberships_self_delete ON memberships FOR DELETE "
            f"USING (person_id = {PRINCIPAL})"
        ),
        # Renaming a cove is editing your own membership, and nobody else's.
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
    """Frozen copy of ``reef.rls.identity_grant_statements``.

    :returns: SQL statements to execute in order
    """
    statements: list[str] = []
    for role in _EXECUTOR_ROLES:
        statements.append(
            f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
            f"'{role}') THEN EXECUTE 'REVOKE UPDATE ON coves FROM {role}'; "
            f"EXECUTE 'GRANT UPDATE (version) ON coves TO {role}'; "
            f"END IF; END $do$"
        )
    return statements


def alias_statements() -> list[str]:
    """Frozen copy of ``reef.rls.alias_statements``.

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
            "reef_admit_member(p_cove uuid, p_person uuid, p_alias text, p_role text)",
            f"""
DECLARE
  candidate text;
  suffix int := 1;
BEGIN
  -- Only the cove's owner admits anybody, matching memberships_insert.
  IF NOT EXISTS (SELECT 1 FROM coves WHERE id = p_cove
                 AND owner_person_id = {PRINCIPAL}) THEN
    RETURN NULL;
  END IF;
  -- The role is decided here, where the row is written, so no caller can
  -- smuggle in a third value the write policies have never heard of.
  IF p_role NOT IN ('member', 'viewer') THEN
    RETURN NULL;
  END IF;
  IF EXISTS (SELECT 1 FROM memberships WHERE cove_id = p_cove
             AND person_id = p_person) THEN
    RETURN NULL;
  END IF;
  -- The alias has to be free for the *invitee*, and the owner cannot see
  -- the invitee's other memberships -- persons are self-only and
  -- memberships are visible per cove, so evaluated in Python this check
  -- would come back clear and the insert would hit the unique constraint.
  -- In here it is both correct and atomic: no window between choosing a
  -- name and taking it.
  --
  -- 'personal' is refused outright rather than suffixed. It is the one
  -- alias whose meaning is fixed for everybody, and a cove admitted under
  -- it would shadow the private cove in every later call.
  candidate := p_alias;
  IF candidate = 'personal' OR candidate IS NULL OR candidate = '' THEN
    candidate := 'cove';
  END IF;
  WHILE EXISTS (SELECT 1 FROM memberships WHERE person_id = p_person
                AND alias = candidate) LOOP
    suffix := suffix + 1;
    candidate := p_alias || '-' || suffix;
    -- A person with thousands of coves is not a case worth looping over.
    IF suffix > 999 THEN
      RETURN NULL;
    END IF;
  END LOOP;
  INSERT INTO memberships (person_id, cove_id, role, alias)
  VALUES (p_person, p_cove, p_role, candidate);
  RETURN candidate;
END
""",
            returns="text",
            writes=("memberships",),
            reads=("coves",),
            volatility="VOLATILE",
            language="plpgsql",
        )
    )
    # memberships_self_update admits the whole row; only the alias may
    # actually move. role and cove_id stay out of reach of the app role, so a
    # member cannot promote themselves or graft their membership onto another
    # cove. Ownership transfer changes role from inside a definer function,
    # which a grant on the caller does not constrain.
    statements += [
        f"DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = "
        f"'{role}') THEN EXECUTE 'REVOKE UPDATE ON memberships FROM {role}'; "
        f"EXECUTE 'GRANT UPDATE (alias) ON memberships TO {role}'; "
        f"END IF; END $do$"
        for role in _EXECUTOR_ROLES
    ]
    return statements


_PERSON_SELF_COLUMNS = "display_name, avatar_mime, avatar_bytes, session_epoch"


def person_column_grant_statements() -> list[str]:
    """Frozen copy of ``reef.rls.person_column_grant_statements``.

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
    """Frozen copy of ``reef.rls.drop_identity_policy_statements``.

    :returns: SQL statements to execute in order
    """
    policies = {
        "persons": ("self_select", "self_update", "self_delete", "invite_insert"),
        "coves": (
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
            f"'{role}') THEN EXECUTE 'GRANT UPDATE ON coves TO {role}'; "
            f"EXECUTE 'GRANT UPDATE ON memberships TO {role}'; "
            f"END IF; END $do$"
        )
    for table in reversed(_IDENTITY_TABLES):
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return statements


def mutation_statements() -> list[str]:
    """Frozen copy of ``reef.rls.mutation_statements``.

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
        # The parameter keeps its old name, alone among these. Policies call
        # this function, so it cannot be dropped and recreated the way the
        # others are -- dropping it needs CASCADE, which takes the policies
        # with it -- and CREATE OR REPLACE refuses to rename a parameter.
        # The name is invisible: every caller passes positionally.
        *_function_ddl(
            "reef_owns_cove(p_space uuid)",
            "SELECT EXISTS (SELECT 1 FROM coves WHERE id = p_space "
            f"AND owner_person_id = {PRINCIPAL})",
            returns="boolean",
            reads=("coves",),
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
            "reef_remove_member(p_cove uuid, p_person uuid, "
            "OUT removed boolean, OUT person_erased boolean)",
            f"""
DECLARE
  affected int;
BEGIN
  removed := false;
  person_erased := false;
  -- Only the cove's owner, and never on themselves: an owner removing
  -- themselves would leave the cove with no accountable person.
  IF NOT EXISTS (SELECT 1 FROM coves WHERE id = p_cove
                 AND owner_person_id = {PRINCIPAL}) THEN
    RETURN;
  END IF;
  IF p_person = {PRINCIPAL} THEN
    RETURN;
  END IF;
  DELETE FROM memberships WHERE cove_id = p_cove AND person_id = p_person;
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
            reads=("coves",),
            volatility="VOLATILE",
            language="plpgsql",
        ),
        *_function_ddl(
            "reef_transfer_cove_ownership(p_cove uuid, p_successor uuid)",
            f"""
BEGIN
  IF NOT EXISTS (SELECT 1 FROM coves WHERE id = p_cove
                 AND owner_person_id = {PRINCIPAL}) THEN
    RETURN false;
  END IF;
  -- The successor must already belong to the cove. Without this an owner
  -- could hand a cove to any uuid that satisfies the foreign key, including
  -- somebody who has never seen it.
  IF NOT EXISTS (SELECT 1 FROM memberships WHERE cove_id = p_cove
                 AND person_id = p_successor) THEN
    RETURN false;
  END IF;
  UPDATE memberships SET role = 'member'
   WHERE cove_id = p_cove AND person_id = p_successor AND role <> 'member';
  UPDATE coves SET owner_person_id = p_successor WHERE id = p_cove;
  RETURN true;
END
""",
            returns="boolean",
            writes=("memberships", "coves"),
            volatility="VOLATILE",
            language="plpgsql",
        ),
    ]


_PERSON_ROW = "TABLE(person_id uuid, person_email text, person_display_name text)"


_PERSON_COLUMNS = "SELECT p.id, p.email, p.display_name FROM persons p"


_SEAT_LOCK_KEY = 0x0DD00DD0


def identity_statements() -> list[str]:
    """Frozen copy of ``reef.rls.identity_statements``.

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
    """Frozen copy of ``reef.rls.session_epoch_statements``.

    :returns: SQL statements to execute in order
    """
    return _function_ddl(
        "reef_person_session_epoch(p_id uuid)",
        "SELECT session_epoch FROM persons WHERE id = p_id",
        returns="integer",
        reads=("persons",),
    )


def appearance_statements() -> list[str]:
    """Frozen copy of ``reef.rls.appearance_statements``.

    :returns: SQL statements to execute in order
    """
    predicate = f"person_id = {PRINCIPAL}"
    return [
        "ALTER TABLE cove_appearances ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE cove_appearances FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS cove_appearances_self ON cove_appearances",
        (
            f"CREATE POLICY cove_appearances_self ON cove_appearances "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        ),
    ]


def open_door_statements() -> list[str]:
    """Frozen copy of ``reef.rls.open_door_statements``.

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
    """Frozen copy of ``reef.rls.avatar_statements``.

    :returns: SQL statements to execute in order
    """
    target_is_member = (
        "p.id IN (SELECT person_id FROM memberships WHERE cove_id = p_cove)"
    )
    return [
        *_function_ddl(
            "reef_member_faces(p_cove uuid)",
            "SELECT p.id, octet_length(p.avatar_bytes)::int FROM persons p "
            f"WHERE {target_is_member} AND p.avatar_bytes IS NOT NULL "
            f"AND {_CALLER_IS_MEMBER}",
            returns="TABLE(person_id uuid, avatar_len int)",
            reads=("persons", "memberships"),
        ),
        *_function_ddl(
            "reef_member_avatar(p_cove uuid, p_person uuid)",
            "SELECT p.avatar_mime, p.avatar_bytes FROM persons p "
            f"WHERE p.id = p_person AND {target_is_member} "
            f"AND p.avatar_bytes IS NOT NULL AND {_CALLER_IS_MEMBER}",
            returns="TABLE(avatar_mime text, avatar_bytes bytea)",
            reads=("persons", "memberships"),
        ),
    ]


JOB_LEASE_SECONDS = 300


JOB_MAX_ATTEMPTS = 5


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
    """Frozen copy of ``reef.rls.job_statements``.

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


_LINK_READ = (
    "from_page_id IN (SELECT id FROM pages) AND to_page_id IN (SELECT id FROM pages)"
)


_LINK_WRITE = (
    "from_page_id IN (SELECT id FROM pages "
    "WHERE cove_id IN (SELECT reef_member_cove_ids())) "
    "AND to_page_id IN (SELECT id FROM pages) "
    f"AND author_id = {PRINCIPAL} "
    # Never from a shared cove into a personal one. The reader rule above
    # would hide such a link from the other members anyway; this keeps it
    # from being stored at all, so no later mistake can reveal it.
    "AND (NOT EXISTS (SELECT 1 FROM pages t JOIN coves c ON c.id = t.cove_id "
    "WHERE t.id = links.to_page_id AND c.kind = 'personal') "
    "OR EXISTS (SELECT 1 FROM pages f JOIN coves c ON c.id = f.cove_id "
    "WHERE f.id = links.from_page_id AND c.kind = 'personal'))"
)


_RETIRE_LINKS = f"""
    UPDATE links l SET status = 'superseded', superseded_at = LOCALTIMESTAMP
    FROM pages p
    WHERE p.id = p_page
      AND l.from_page_id = p.id
      AND p.cove_id IN (
          SELECT cove_id FROM memberships
          WHERE person_id = {PRINCIPAL} AND role = 'member'
      )
      AND l.source = 'wikilink'
      AND l.status = 'active'
      AND NOT (l.target_text = ANY (COALESCE(p.reference_targets, '{{}}')))
"""


def link_statements() -> list[str]:
    """Frozen copy of ``reef.rls.link_statements``.

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
        "links_not_to_itself": "from_page_id <> to_page_id",
    }
    statements: list[str] = []
    for name, check in checks.items():
        statements.append(f"ALTER TABLE links DROP CONSTRAINT IF EXISTS {name}")
        statements.append(f"ALTER TABLE links ADD CONSTRAINT {name} CHECK ({check})")
    statements += [
        # One live link of a kind between two pages.
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS links_one_active "
            "ON links (from_page_id, to_page_id, kind) WHERE status = 'active'"
        ),
        # Backlinks: "what points here".
        (
            "CREATE INDEX IF NOT EXISTS links_inbound "
            "ON links (to_page_id) WHERE status = 'active'"
        ),
        "ALTER TABLE links ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE links FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS links_select ON links",
        "DROP POLICY IF EXISTS links_insert ON links",
        f"CREATE POLICY links_select ON links FOR SELECT USING ({_LINK_READ})",
        f"CREATE POLICY links_insert ON links FOR INSERT WITH CHECK ({_LINK_WRITE})",
        *_function_ddl(
            "reef_retire_links(p_page uuid)",
            _RETIRE_LINKS,
            returns="void",
            reads=("pages", "memberships"),
            writes=("links",),
            volatility="VOLATILE",
        ),
    ]
    return statements


_USAGE_PREDICATE = f"person_id = {PRINCIPAL} AND page_id IN (SELECT id FROM pages)"


def usage_statements() -> list[str]:
    """Frozen copy of ``reef.rls.usage_statements``.

    :returns: SQL statements to execute in order
    """
    return [
        "ALTER TABLE page_access DROP CONSTRAINT IF EXISTS page_access_via_known",
        (
            "ALTER TABLE page_access ADD CONSTRAINT page_access_via_known "
            "CHECK (via IN ('read', 'write', 'remember', 'web'))"
        ),
        "ALTER TABLE page_access DROP CONSTRAINT IF EXISTS page_access_weight_sane",
        (
            "ALTER TABLE page_access ADD CONSTRAINT page_access_weight_sane "
            "CHECK (weight > 0 AND weight <= 10)"
        ),
        # What recomputing activation reads: one person's log, newest first
        # per page.
        (
            "CREATE INDEX IF NOT EXISTS page_access_recent "
            "ON page_access (person_id, page_id, accessed_at DESC)"
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS page_heat_person_page "
            "ON page_heat (person_id, page_id)"
        ),
        "ALTER TABLE page_access ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE page_access FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS page_access_select ON page_access",
        "DROP POLICY IF EXISTS page_access_insert ON page_access",
        "DROP POLICY IF EXISTS page_access_delete ON page_access",
        (
            f"CREATE POLICY page_access_select ON page_access FOR SELECT "
            f"USING ({_USAGE_PREDICATE})"
        ),
        (
            f"CREATE POLICY page_access_insert ON page_access FOR INSERT "
            f"WITH CHECK ({_USAGE_PREDICATE})"
        ),
        (
            f"CREATE POLICY page_access_delete ON page_access FOR DELETE "
            f"USING (person_id = {PRINCIPAL})"
        ),
        "ALTER TABLE page_heat ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE page_heat FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS page_heat_owner ON page_heat",
        (
            f"CREATE POLICY page_heat_owner ON page_heat "
            f"USING ({_USAGE_PREDICATE}) WITH CHECK ({_USAGE_PREDICATE})"
        ),
    ]


SEARCH_LOG_DAYS = 90


SEARCH_LOG_MAX_QUERY = 500


_PURGE_SEARCH_LOG = f"""
DECLARE
    removed integer;
BEGIN
    DELETE FROM search_log
    WHERE searched_at < LOCALTIMESTAMP - make_interval(days => {SEARCH_LOG_DAYS});
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
"""


def search_log_statements() -> list[str]:
    """Frozen copy of ``reef.rls.search_log_statements``.

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
            "CHECK (cardinality(page_ids) <= 50)"
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
    ]


_EMBEDDING_READ = "page_id IN (SELECT id FROM pages)"


_EMBEDDING_WRITE = (
    "page_id IN (SELECT id FROM pages WHERE cove_id IN (SELECT reef_member_cove_ids()))"
)


def embedding_statements() -> list[str]:
    """Frozen copy of ``reef.rls.embedding_statements``.

    :returns: SQL statements to execute in order
    """
    own = f"person_id = {PRINCIPAL}"
    return [
        "ALTER TABLE page_embeddings DROP CONSTRAINT IF EXISTS page_embeddings_dims",
        (
            "ALTER TABLE page_embeddings ADD CONSTRAINT page_embeddings_dims "
            "CHECK (dims = vector_dims(embedding) AND dims BETWEEN 1 AND 4096)"
        ),
        # One vector per piece and model. COALESCE because NULL, the whole
        # page, must count as one value and not as "never equal".
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS page_embeddings_piece "
            "ON page_embeddings (page_id, model_id, COALESCE(section_anchor, ''))"
        ),
        "ALTER TABLE page_embeddings ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE page_embeddings FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS page_embeddings_select ON page_embeddings",
        "DROP POLICY IF EXISTS page_embeddings_insert ON page_embeddings",
        "DROP POLICY IF EXISTS page_embeddings_delete ON page_embeddings",
        (
            f"CREATE POLICY page_embeddings_select ON page_embeddings "
            f"FOR SELECT USING ({_EMBEDDING_READ})"
        ),
        (
            f"CREATE POLICY page_embeddings_insert ON page_embeddings "
            f"FOR INSERT WITH CHECK ({_EMBEDDING_WRITE})"
        ),
        (
            f"CREATE POLICY page_embeddings_delete ON page_embeddings "
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


SEARCH_CONFIGS = ("english", "dutch")


INDEXED_CHARS = 200_000


def _vector(heading: str, content: str) -> str:
    """Frozen copy of ``reef.search._vector``.

    :param heading: SQL expression for the short identifying text
    :param content: SQL expression for the long text
    :returns: a ``tsvector`` expression
    """
    return " || ".join(
        f"setweight(to_tsvector('{config}', coalesce({heading}, '')), 'A') || "
        f"setweight(to_tsvector('{config}', "
        f"left(coalesce({content}, ''), {INDEXED_CHARS})), 'B')"
        for config in SEARCH_CONFIGS
    )


def search_statements() -> list[str]:
    """Frozen copy of ``reef.search.search_statements``.

    :returns: SQL statements to execute in order
    """
    statements = []
    for table, heading, content in (
        ("pages", "title", "body"),
        ("attachments", "filename", "description"),
    ):
        statements.append(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS search_vector "
            f"tsvector GENERATED ALWAYS AS ({_vector(heading, content)}) STORED"
        )
    return statements
