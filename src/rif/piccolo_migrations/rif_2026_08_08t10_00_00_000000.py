"""Generalize the two-tier household into named, owned, invitable spaces.

Four schema moves and three data moves, in an order that matters:

1. ``memberships.role`` arrives with a ``'member'`` default, so every existing
   membership keeps exactly the authority it had.
2. ``persons`` gains the invite audit trail (``invited_by_person_id``,
   ``created_at``).
3. ``promotions.dest_space_id`` arrives nullable, is backfilled to the single
   pre-existing shared space, and only then becomes ``NOT NULL`` with its
   foreign key. Sharing used to have one possible destination, so the
   backfill is exact rather than a guess.
4. ``spaces.kind`` ``'household'`` becomes ``'shared'`` -- *after* step 3,
   whose backfill matches on ``'household'``. ``kind`` is a plain ``VARCHAR``
   here (Piccolo's ``choices`` are validated in Python, not by the database),
   so this is an ordinary ``UPDATE`` and not the enum-value rename the
   SQLAlchemy original needed.
5. Every space gets an owner: for a space that predates ownership, the member
   with the lowest ``person_id``. That pick is arbitrary but deterministic;
   ``docs/runbook.md`` tells the operator how to verify it and reassign with
   one ``UPDATE`` before anyone relies on owner-only administration.
6. The blanket unique constraint on ``spaces.owner_person_id`` is dropped
   **before** that backfill, not after. The seeded shared space's lowest-id
   member already owns a personal space, so backfilling under a blanket
   constraint would collide with that ownership. The partial index that
   replaces it constrains only ``kind = 'personal'``, so it never sees the
   collision.

RLS is re-applied last, from :mod:`rif.rls` **imported live** -- this is the
newest policy migration, so it is the one that must not drift from the module
tests apply. ``disable_statements`` runs *first*, before any data move, for a
reason specific to this project's shape: ``promotions`` carries ``FORCE ROW
LEVEL SECURITY``, which subjects the table's own owner to its policy, and the
migration role is that owner in local development (``migration_dsn`` falls
back to the app DSN). With the policy live and no ``app.person_id`` bound, the
``dest_space_id`` backfill would see zero rows and the following ``SET NOT
NULL`` would then fail on the rows it could not reach. Turning enforcement off
for the duration is also what lets the backfills see the whole table.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import DB
from rif.rls import disable_statements, enable_statements

ID = "2026-08-08T10:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "multi-user spaces"

# Postgres names an inline column constraint itself, and the name is not part
# of any migration this project wrote -- Piccolo emitted ``unique=True`` on
# ``spaces.owner_person_id`` and the server chose ``spaces_owner_person_id_key``.
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
    WHERE c.conrelid = 'spaces'::regclass
      AND c.contype = 'u'
      AND c.conkey = ARRAY[(
          SELECT a.attnum FROM pg_attribute a
          WHERE a.attrelid = 'spaces'::regclass
            AND a.attname = 'owner_person_id'
      )]::smallint[];
    IF target IS NOT NULL THEN
        EXECUTE format('ALTER TABLE spaces DROP CONSTRAINT %I', target);
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
    "ALTER TABLE promotions ADD COLUMN dest_space_id UUID",
    (
        "UPDATE promotions SET dest_space_id = "
        "(SELECT id FROM spaces WHERE kind = 'household' ORDER BY slug LIMIT 1)"
    ),
    "ALTER TABLE promotions ALTER COLUMN dest_space_id SET NOT NULL",
    (
        "ALTER TABLE promotions ADD CONSTRAINT promotions_dest_space_id_fkey "
        "FOREIGN KEY (dest_space_id) REFERENCES spaces(id)"
    ),
    # Spaces are named groups, not a fixed household tier.
    "UPDATE spaces SET kind = 'shared' WHERE kind = 'household'",
    # Ownership: drop the blanket constraint before the backfill can collide.
    _DROP_BLANKET_OWNER_UNIQUE,
    (
        "UPDATE spaces s SET owner_person_id = "
        "(SELECT m.person_id FROM memberships m WHERE m.space_id = s.id "
        "ORDER BY m.person_id LIMIT 1) "
        "WHERE s.owner_person_id IS NULL"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_personal_owner_person ON spaces "
        "(owner_person_id) WHERE kind = 'personal'"
    ),
    "ALTER TABLE spaces ALTER COLUMN owner_person_id SET NOT NULL",
]


async def forwards() -> MigrationManager:
    """Apply the schema and data moves, then re-arm RLS from ``rif.rls``.

    :returns: the migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        """Execute the DDL and backfills, in order, with RLS off in between."""
        for statement in disable_statements():
            await DB._run_in_new_connection(statement)
        for statement in STATEMENTS:
            await DB._run_in_new_connection(statement)
        for statement in enable_statements():
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
    ``'household'``, drop the column the current ``rif.rls`` predicates
    reference, and then re-create pre-role policies to match -- and it cannot
    put back the ``owner_person_id`` values it would clear. Restoring from a
    backup is the supported path (``docs/restore.md``).

    :raises RuntimeError: always
    """
    raise RuntimeError(
        "multi-user spaces cannot be reversed in place: the owner backfill and "
        "the kind rename are lossy, and rif.rls's live predicates require "
        "memberships.role. Restore from a backup instead -- docs/restore.md."
    )
