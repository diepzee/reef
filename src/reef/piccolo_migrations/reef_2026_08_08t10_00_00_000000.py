"""Generalize the two-tier household into named, owned, invitable coves.

Four schema moves and three data moves, in an order that matters:

1. ``memberships.role`` arrives with a ``'member'`` default, so every existing
   membership keeps exactly the authority it had.
2. ``persons`` gains the invite audit trail (``invited_by_person_id``,
   ``created_at``).
3. ``promotions.dest_cove_id`` arrives nullable, is backfilled to the single
   pre-existing shared cove, and only then becomes ``NOT NULL`` with its
   foreign key. Sharing used to have one possible destination, so the
   backfill is exact rather than a guess.
4. ``coves.kind`` ``'household'`` becomes ``'shared'`` -- *after* step 3,
   whose backfill matches on ``'household'``. ``kind`` is a plain ``VARCHAR``
   here (Piccolo's ``choices`` are validated in Python, not by the database),
   so this is an ordinary ``UPDATE`` and not the enum-value rename the
   SQLAlchemy original needed.
5. Every cove gets an owner: for a cove that predates ownership, the member
   with the lowest ``person_id``. That pick is arbitrary but deterministic;
   ``docs/runbook.md`` tells the operator how to verify it and reassign with
   one ``UPDATE`` before anyone relies on owner-only administration.
6. The blanket unique constraint on ``coves.owner_person_id`` is dropped
   **before** that backfill, not after. The seeded shared cove's lowest-id
   member already owns a personal cove, so backfilling under a blanket
   constraint would collide with that ownership. The partial index that
   replaces it constrains only ``kind = 'personal'``, so it never sees the
   collision.

RLS is re-applied last, from :mod:`reef.rls` **imported live** -- this is the
newest policy migration, so it is the one that must not drift from the module
tests apply. ``disable_statements`` runs *first*, before any data move, for a
reason specific to this project's shape: ``promotions`` carries ``FORCE ROW
LEVEL SECURITY``, which subjects the table's own owner to its policy, and the
migration role is that owner in local development (``migration_dsn`` falls
back to the app DSN). With the policy live and no ``app.person_id`` bound, the
``dest_cove_id`` backfill would see zero rows and the following ``SET NOT
NULL`` would then fail on the rows it could not reach. Turning enforcement off
for the duration is also what lets the backfills see the whole table.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB

ID = "2026-08-08T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "multi-user coves"


# --- frozen snapshot -------------------------------------------------------
#
# These lists held `disable_statements()` and `enable_statements()` imported
# live from `reef.rls`. That import is what made this migration a moving
# target: it runs against the schema of its own day, while the functions it
# called kept growing new columns. `session_epoch` and `memberships.alias`
# both landed inside `enable_statements` and broke every build from scratch,
# silently, because production was already past this point.
#
# Frozen here as the DDL actually stood on the day, following
# `2026-08-06T12:20:00`. History is inert; the newest migration is the one
# that re-applies today's policies, so drift has exactly one place to live.


_FROZEN_DISABLE = [
    "DROP POLICY IF EXISTS promotions_owner ON promotions",
    "ALTER TABLE promotions NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE promotions DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS revisions_select ON revisions",
    "DROP POLICY IF EXISTS revisions_insert ON revisions",
    "DROP POLICY IF EXISTS revisions_update ON revisions",
    "DROP POLICY IF EXISTS revisions_delete ON revisions",
    "DROP POLICY IF EXISTS revisions_member ON revisions",
    "ALTER TABLE revisions NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE revisions DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS attachments_select ON attachments",
    "DROP POLICY IF EXISTS attachments_insert ON attachments",
    "DROP POLICY IF EXISTS attachments_update ON attachments",
    "DROP POLICY IF EXISTS attachments_delete ON attachments",
    "DROP POLICY IF EXISTS attachments_member ON attachments",
    "ALTER TABLE attachments NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE attachments DISABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS pages_select ON pages",
    "DROP POLICY IF EXISTS pages_insert ON pages",
    "DROP POLICY IF EXISTS pages_update ON pages",
    "DROP POLICY IF EXISTS pages_delete ON pages",
    "DROP POLICY IF EXISTS pages_member ON pages",
    "ALTER TABLE pages NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE pages DISABLE ROW LEVEL SECURITY",
]

_FROZEN_ENABLE = [
    "ALTER TABLE pages ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE pages FORCE ROW LEVEL SECURITY",
    "CREATE POLICY pages_select ON pages FOR SELECT USING (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid))",
    "CREATE POLICY pages_insert ON pages FOR INSERT WITH CHECK (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'))",
    "CREATE POLICY pages_update ON pages FOR UPDATE USING (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member')) WITH CHECK (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'))",
    "CREATE POLICY pages_delete ON pages FOR DELETE USING (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'))",
    "ALTER TABLE attachments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE attachments FORCE ROW LEVEL SECURITY",
    "CREATE POLICY attachments_select ON attachments FOR SELECT USING (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid))",
    "CREATE POLICY attachments_insert ON attachments FOR INSERT WITH CHECK (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'))",
    "CREATE POLICY attachments_update ON attachments FOR UPDATE USING (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member')) WITH CHECK (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'))",
    "CREATE POLICY attachments_delete ON attachments FOR DELETE USING (cove_id IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND role = 'member'))",
    "ALTER TABLE revisions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE revisions FORCE ROW LEVEL SECURITY",
    "CREATE POLICY revisions_select ON revisions FOR SELECT USING (page_id IN (SELECT p.id FROM pages p JOIN memberships m ON m.cove_id = p.cove_id WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid))",
    "CREATE POLICY revisions_insert ON revisions FOR INSERT WITH CHECK (page_id IN (SELECT p.id FROM pages p JOIN memberships m ON m.cove_id = p.cove_id WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND m.role = 'member'))",
    "CREATE POLICY revisions_update ON revisions FOR UPDATE USING (page_id IN (SELECT p.id FROM pages p JOIN memberships m ON m.cove_id = p.cove_id WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND m.role = 'member')) WITH CHECK (page_id IN (SELECT p.id FROM pages p JOIN memberships m ON m.cove_id = p.cove_id WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND m.role = 'member'))",
    "CREATE POLICY revisions_delete ON revisions FOR DELETE USING (page_id IN (SELECT p.id FROM pages p JOIN memberships m ON m.cove_id = p.cove_id WHERE m.person_id = NULLIF(current_setting('app.person_id', true), '')::uuid AND m.role = 'member'))",
    "ALTER TABLE promotions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE promotions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS promotions_owner ON promotions",
    "CREATE POLICY promotions_owner ON promotions USING (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK (person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)",
]

# Postgres names an inline column constraint itself, and the name is not part
# of any migration this project wrote -- Piccolo emitted ``unique=True`` on
# ``coves.owner_person_id`` and the server chose ``coves_owner_person_id_key``.
# Looking the name up by shape rather than hardcoding it keeps the migration
# correct against a database whose constraint was named differently (a restore
# from ``pg_dump --no-owner`` into a differently-named table, for instance),
# and makes it a no-op where the constraint is already gone.
_DROP_BLANKET_OWNER_UNIQUE = """
DO $$
DECLARE target text;
BEGIN
    SELECT c.conname INTO target
    FROM pg_constraint c
    WHERE c.conrelid = 'coves'::regclass
      AND c.contype = 'u'
      AND c.conkey = ARRAY[(
          SELECT a.attnum FROM pg_attribute a
          WHERE a.attrelid = 'coves'::regclass
            AND a.attname = 'owner_person_id'
      )]::smallint[];
    IF target IS NOT NULL THEN
        EXECUTE format('ALTER TABLE coves DROP CONSTRAINT %I', target);
    END IF;
END $$
"""

STATEMENTS = [
    # Roles: dormant VIEWER, but enforced by the policies below from day one.
    "ALTER TABLE memberships ADD COLUMN role VARCHAR DEFAULT 'member' NOT NULL",
    # Invite audit trail.
    "ALTER TABLE persons ADD COLUMN invited_by_person_id UUID REFERENCES persons(id)",
    "ALTER TABLE persons ADD COLUMN created_at TIMESTAMP DEFAULT now() NOT NULL",
    # A share now names its destination; before this there was only one.
    "ALTER TABLE promotions ADD COLUMN dest_cove_id UUID",
    (
        "UPDATE promotions SET dest_cove_id = "
        "(SELECT id FROM coves WHERE kind = 'household' ORDER BY slug LIMIT 1)"
    ),
    "ALTER TABLE promotions ALTER COLUMN dest_cove_id SET NOT NULL",
    (
        "ALTER TABLE promotions ADD CONSTRAINT promotions_dest_cove_id_fkey "
        "FOREIGN KEY (dest_cove_id) REFERENCES coves(id)"
    ),
    # Coves are named groups, not a fixed household tier.
    "UPDATE coves SET kind = 'shared' WHERE kind = 'household'",
    # Ownership: drop the blanket constraint before the backfill can collide.
    _DROP_BLANKET_OWNER_UNIQUE,
    (
        "UPDATE coves s SET owner_person_id = "
        "(SELECT m.person_id FROM memberships m WHERE m.cove_id = s.id "
        "ORDER BY m.person_id LIMIT 1) "
        "WHERE s.owner_person_id IS NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_personal_owner_person ON coves "
        "(owner_person_id) WHERE kind = 'personal'"
    ),
    "ALTER TABLE coves ALTER COLUMN owner_person_id SET NOT NULL",
]


async def forwards() -> MigrationManager:
    """Apply the schema and data moves, then re-arm RLS from ``reef.rls``.

    :returns: the migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Execute the DDL and backfills, in order, with RLS off in between."""
        for statement in _FROZEN_DISABLE:
            await DB._run_in_new_connection(statement)
        for statement in STATEMENTS:
            await DB._run_in_new_connection(statement)
        for statement in _FROZEN_ENABLE:
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    manager.add_raw_backwards(_refuse_backwards)
    return manager


async def _refuse_backwards() -> None:
    """Refuse to reverse this migration rather than half-reverse it.

    The other migrations in this app register no backwards step at all, so
    ``piccolo migrations backwards`` reports success on them while changing
    nothing. That silence is tolerable for additive DDL and actively dangerous
    here: reversing would have to re-collapse ``'shared'`` into
    ``'household'``, drop the column the current ``reef.rls`` predicates
    reference, and then re-create pre-role policies to match -- and it cannot
    put back the ``owner_person_id`` values it would clear. Restoring from a
    backup is the supported path (``docs/restore.md``).

    :raises RuntimeError: always
    """
    raise RuntimeError(
        "multi-user coves cannot be reversed in place: the owner backfill and "
        "the kind rename are lossy, and reef.rls's live predicates require "
        "memberships.role. Restore from a backup instead -- docs/restore.md."
    )
