"""Move cove names off the cove and onto each person's membership."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.rls import (
    alias_statements,
    identity_grant_statements,
    mutation_statements,
)

ID = "2026-08-13T12:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "memberships.alias, per-person uniqueness, admit function"


async def forwards() -> MigrationManager:
    """Make a cove's name a property of the reader rather than the cluster.

    ``spaces.slug`` was ``UNIQUE``, which made cove names one namespace
    shared by every tenant. Three things followed, all bad: the first person
    to take ``family`` took it from everybody who would ever sign up; a name
    already used by a stranger failed against a constraint the creator's own
    policies made invisible, so the refusal arrived as a raw driver error;
    and that error was an existence oracle across tenants.

    The rule that actually has to hold is narrower -- *the names one person
    can reach are unique* -- and it is not a property of a cove at all. It
    moves to ``memberships.alias`` with ``UNIQUE (person_id, alias)``, an
    ordinary index over exactly the right rows.

    The backfill is total because today's slugs are globally unique: every
    member of a cove gets that slug as their alias with no possible
    collision, and every personal membership gets the fixed ``personal``.
    Nobody's cove changes name on deploy.

    Order matters and is not negotiable:

    1. Add the column ``NOT NULL DEFAULT ''`` so existing rows are valid.
    2. Fill it, personal first, before any uniqueness is demanded.
    3. Only then add the constraint -- added earlier it would reject every
       row at once, since they would all share the empty-string default.
    4. Drop ``spaces_slug_key`` last, once nothing depends on it.

    All in one transaction: a database left half-way through this has a
    column the application reads and no values in it, which resolves every
    cove name to nothing and locks everybody out of their own memory.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically(
            [
                (
                    "ALTER TABLE memberships ADD COLUMN IF NOT EXISTS alias "
                    "VARCHAR NOT NULL DEFAULT ''"
                ),
                # The backfill is a bulk UPDATE with no principal armed, and
                # every table here carries FORCE ROW LEVEL SECURITY -- which
                # subjects the table's *owner* to the policies too. Left on,
                # the three statements below match no rows at all, every
                # alias keeps the empty default, and the constraint further
                # down then rejects the second membership of every person.
                #
                # It happens to work on Railway, whose migration credential
                # is the cluster's bootstrap superuser and so bypasses row
                # security outright. That is a property of one deployment,
                # not of this migration, and the README is explicit that a
                # properly least-privileged one would not have it. So the
                # enforcement is lifted here explicitly rather than relied on
                # to be absent: inside this transaction, holding the ACCESS
                # EXCLUSIVE lock the ALTER above already took, so no request
                # ever sees the table with its policies relaxed.
                #
                # Both tables, not just the one being written: each statement
                # below reads ``spaces`` to decide what the alias should be,
                # and a filtered join contributes no rows just as silently as
                # a filtered update. Lifted, the fallback further down fires
                # for every row and renames every cove to ``cove-<n>``.
                "ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY",
                "ALTER TABLE spaces NO FORCE ROW LEVEL SECURITY",
                # The personal space is addressed by a fixed name, and its
                # slug (personal-<hex>) is an internal detail that must never
                # become one.
                (
                    "UPDATE memberships m SET alias = 'personal' FROM spaces s "
                    "WHERE s.id = m.space_id AND s.kind = 'personal'"
                ),
                (
                    "UPDATE memberships m SET alias = s.slug FROM spaces s "
                    "WHERE s.id = m.space_id AND s.kind = 'shared'"
                ),
                # Belt and braces: a membership whose space vanished under it
                # would otherwise keep the empty default and collide with any
                # other such row. If this ever fires for a row that *does*
                # have a space, the two statements above silently matched
                # nothing -- see the note on row security above.
                "UPDATE memberships SET alias = 'cove-' || id::text WHERE alias = ''",
                "ALTER TABLE spaces FORCE ROW LEVEL SECURITY",
                "ALTER TABLE memberships FORCE ROW LEVEL SECURITY",
                (
                    "ALTER TABLE memberships DROP CONSTRAINT IF EXISTS "
                    "memberships_person_alias"
                ),
                (
                    "ALTER TABLE memberships ADD CONSTRAINT memberships_person_alias "
                    "UNIQUE (person_id, alias)"
                ),
                # A cove's name is no longer globally unique, and keeping the
                # constraint would go on refusing the squat this migration
                # exists to permit.
                "ALTER TABLE spaces DROP CONSTRAINT IF EXISTS spaces_slug_key",
                # reef_admit_member lands here; it is what picks a free alias
                # for an invitee whose other memberships the inviter cannot
                # see. identity_grant_statements adds the column-level
                # narrowing that lets a person rewrite alias and nothing else.
                *mutation_statements(),
                # reef_admit_member and the GRANT UPDATE (alias) live here
                # rather than in the two groups above, because both are
                # also run by August migrations that predate this column.
                # See alias_statements.
                *alias_statements(),
                "DROP POLICY IF EXISTS memberships_self_update ON memberships",
                (
                    "CREATE POLICY memberships_self_update ON memberships FOR UPDATE "
                    "USING (person_id = NULLIF(current_setting('app.person_id', true), "
                    "'')::uuid) WITH CHECK (person_id = "
                    "NULLIF(current_setting('app.person_id', true), '')::uuid)"
                ),
                *identity_grant_statements(),
            ]
        )

    manager.add_raw(run)
    return manager
