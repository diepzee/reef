"""Apply the composite constraints and the RLS policies.

Piccolo's auto-generated migration builds the tables; it cannot know about
either the multi-column constraints its table syntax can't express or the
row-level security that is this project's actual privacy boundary.

This migration holds a **frozen snapshot** of that DDL rather than importing
``reef.rls`` live, which is what it used to do. It runs before
``memberships.role`` exists, so today's role-aware policies would fail to
compile here: ``CREATE POLICY`` validates its expression against the table as
it stands. Later migrations re-apply the current policies from ``reef.rls``
itself, so the policies under test and the policies in production still cannot
drift apart -- the drift risk lives with the *newest* migration, not this one.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB

ID = "2026-08-06T12:20:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "composite constraints and row-level security policies"

_MEMBER_PREDICATE = (
    "space_id IN (SELECT space_id FROM memberships "
    "WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)"
)

_REVISION_PREDICATE = (
    "page_id IN (SELECT p.id FROM pages p "
    "JOIN memberships m ON m.space_id = p.space_id "
    "WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)"
)

STATEMENTS = [
    (
        "ALTER TABLE memberships ADD CONSTRAINT memberships_person_space "
        "UNIQUE (person_id, space_id)"
    ),
    "ALTER TABLE pages ADD CONSTRAINT pages_space_path UNIQUE (space_id, path)",
    "ALTER TABLE pages ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE pages FORCE ROW LEVEL SECURITY",
    (
        f"CREATE POLICY pages_member ON pages "
        f"USING ({_MEMBER_PREDICATE}) WITH CHECK ({_MEMBER_PREDICATE})"
    ),
    "ALTER TABLE attachments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE attachments FORCE ROW LEVEL SECURITY",
    (
        f"CREATE POLICY attachments_member ON attachments "
        f"USING ({_MEMBER_PREDICATE}) WITH CHECK ({_MEMBER_PREDICATE})"
    ),
    "ALTER TABLE revisions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE revisions FORCE ROW LEVEL SECURITY",
    (
        f"CREATE POLICY revisions_member ON revisions "
        f"USING ({_REVISION_PREDICATE}) WITH CHECK ({_REVISION_PREDICATE})"
    ),
]


async def forwards() -> MigrationManager:
    """Add the constraints and turn on RLS.

    :returns: the migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        """Execute the DDL, in order."""
        for statement in STATEMENTS:
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
