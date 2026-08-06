"""Row-level security policy DDL, shared by the schema migration and tests.

The migration (``migrations/versions/0f1d29c16349_initial_schema.py``)
applies these policies to the real database; ``tests/conftest.py`` applies
them to ``rif_test`` when building the test schema. RLS is the security
boundary for this project, so the two call sites must never be allowed to
drift apart — a difference here would mean tests validate policies that
production does not actually enforce. This module is the single source of
truth both call through.

``NULLIF(current_setting('app.person_id', true), '')`` matters, not just a
bare ``current_setting(..., true)`` cast: a session where ``app.person_id``
was never touched returns NULL and denies as intended, but the principal
-clearing path (``set_config('app.person_id', '', true)``) sets a defined
empty string, not an absence, and ``''::uuid`` raises rather than comparing
false. ``NULLIF`` folds both cases to the same NULL, so both deny cleanly.
"""


def constraint_statements() -> list[str]:
    """Return DDL for constraints Piccolo's table definitions cannot express.

    Piccolo gives every table one surrogate primary key and has no syntax
    for multi-column uniqueness, so three constraints this schema genuinely
    depends on have to be stated here: ``memberships`` is keyed by the pair
    it stores, a page path is unique within its space, and a pending
    promotion is unique per principal, source page and destination. Losing
    any of them would let duplicates through that the application logic
    assumes cannot exist.

    :returns: SQL statements to execute in order
    """
    return [
        (
            "ALTER TABLE memberships ADD CONSTRAINT memberships_person_space "
            "UNIQUE (person_id, space_id)"
        ),
        ("ALTER TABLE pages ADD CONSTRAINT pages_space_path UNIQUE (space_id, path)"),
    ]


_MEMBER_PREDICATE = (
    "space_id IN (SELECT space_id FROM memberships "
    "WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)"
)

_REVISION_PREDICATE = (
    "page_id IN (SELECT p.id FROM pages p "
    "JOIN memberships m ON m.space_id = p.space_id "
    "WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)"
)


def enable_statements() -> list[str]:
    """Return the DDL that turns on and enforces RLS on the content tables.

    Covers ``pages`` and ``attachments`` (membership via their own
    ``space_id``) and ``revisions`` (membership via their page's space).
    ``FORCE ROW LEVEL SECURITY`` extends the policies to the table owner,
    not just other roles.

    :returns: SQL statements to execute in order
    """
    statements: list[str] = []
    for table in ("pages", "attachments"):
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        statements.append(
            f"CREATE POLICY {table}_member ON {table} "
            f"USING ({_MEMBER_PREDICATE}) WITH CHECK ({_MEMBER_PREDICATE})"
        )
    statements.append("ALTER TABLE revisions ENABLE ROW LEVEL SECURITY")
    statements.append("ALTER TABLE revisions FORCE ROW LEVEL SECURITY")
    statements.append(
        "CREATE POLICY revisions_member ON revisions "
        f"USING ({_REVISION_PREDICATE}) WITH CHECK ({_REVISION_PREDICATE})"
    )
    return statements


def disable_statements() -> list[str]:
    """Return the DDL that undoes :func:`enable_statements`.

    Statements run in the reverse order of ``enable_statements``: drop each
    policy before turning enforcement and row security back off.

    :returns: SQL statements to execute in order
    """
    statements: list[str] = [
        "DROP POLICY IF EXISTS revisions_member ON revisions",
        "ALTER TABLE revisions NO FORCE ROW LEVEL SECURITY",
        "ALTER TABLE revisions DISABLE ROW LEVEL SECURITY",
    ]
    for table in ("pages", "attachments"):
        statements.append(f"DROP POLICY IF EXISTS {table}_member ON {table}")
        statements.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return statements
