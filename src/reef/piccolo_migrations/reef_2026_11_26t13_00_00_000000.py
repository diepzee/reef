"""Erase private usage when a membership ends.

Replaces reef_remove_member(uuid, uuid) so members can leave and owners can
remove them, deleting their engram_access and engram_heat rows in the same
statement. The current owner must transfer ownership before leaving.
The helper keeps its owner and execution grants, including rif_authz and
rif_app on databases whose roles still have the old names. Its search path
is pg_catalog, public, pg_temp.

Grants the existing function owner SELECT on engrams and SELECT, DELETE on
engram_access and engram_heat. No application role gains access to another
person's rows. Drops memberships_self_delete, so application callers must
use the helper to end a membership and cannot skip usage cleanup. Table
definitions, indexes, other row policies, other functions and search_log
are left alone. The SQL is fixed text and runs atomically, safely twice.

This deletes data: existing engram_access and engram_heat rows whose person
is neither a member nor the owner of the engram's space. Production had
zero such rows in the read-only count on 26 Nov 2026. The cleanup temporarily
uses the existing helper owner's role to see rows hidden by row security,
then restores the migration connection's role. It creates no extra helper
and changes no role attributes or memberships.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-26T13:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "erase private usage when membership ends"

STATEMENTS = [
    """CREATE OR REPLACE FUNCTION reef_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean) RETURNS record LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS $reef$
DECLARE
  affected int;
BEGIN
  removed := false;
  person_erased := false;
  -- Wait for an ownership change before deciding who may leave.
  PERFORM 1 FROM spaces WHERE id = p_space FOR UPDATE;
  -- A member may leave, or the owner may remove them. The current owner
  -- must hand ownership on before leaving.
  IF NULLIF(current_setting('app.person_id', true), '')::uuid IS NULL OR
     (p_person IS DISTINCT FROM NULLIF(current_setting('app.person_id', true), '')::uuid AND
      NOT EXISTS (SELECT 1 FROM spaces WHERE id = p_space
                  AND owner_person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)) THEN
    RETURN;
  END IF;
  IF EXISTS (SELECT 1 FROM spaces WHERE id = p_space
             AND owner_person_id = p_person) THEN
    RETURN;
  END IF;
  DELETE FROM memberships WHERE space_id = p_space AND person_id = p_person;
  GET DIAGNOSTICS affected = ROW_COUNT;
  IF affected = 0 THEN
    RETURN;
  END IF;
  removed := true;
  DELETE FROM engram_access a USING engrams e
   WHERE a.engram_id = e.id AND e.space_id = p_space
     AND a.person_id = p_person;
  DELETE FROM engram_heat h USING engrams e
   WHERE h.engram_id = e.id AND e.space_id = p_space
     AND h.person_id = p_person;
  -- An invitation that was never taken up: no provider subject bound, and
  -- now no membership anywhere. Erasing it is the typo-repair path.
  IF EXISTS (SELECT 1 FROM persons WHERE id = p_person AND subject IS NULL)
     AND NOT EXISTS (SELECT 1 FROM memberships WHERE person_id = p_person) THEN
    DELETE FROM persons WHERE id = p_person;
    person_erased := true;
  END IF;
END
$reef$""",
    """DO $rights$
DECLARE
    authz name;
BEGIN
    SELECT pg_get_userbyid(proowner) INTO STRICT authz
    FROM pg_proc WHERE oid = 'reef_remove_member(uuid,uuid)'::regprocedure;
    EXECUTE format('GRANT SELECT ON engrams TO %I', authz);
    EXECUTE format('GRANT SELECT, DELETE ON engram_access, engram_heat TO %I', authz);
END
$rights$""",
    """DO $cleanup$
DECLARE
    authz name;
    previous_role text := current_setting('role');
BEGIN
    SELECT pg_get_userbyid(proowner) INTO STRICT authz
    FROM pg_proc WHERE oid = 'reef_remove_member(uuid,uuid)'::regprocedure;
    EXECUTE format('SET LOCAL ROLE %I', authz);
    DELETE FROM engram_access a USING engrams e, spaces s
    WHERE a.engram_id = e.id AND e.space_id = s.id
      AND s.owner_person_id <> a.person_id
      AND NOT EXISTS (
          SELECT 1 FROM memberships m
          WHERE m.space_id = s.id AND m.person_id = a.person_id
      );
    DELETE FROM engram_heat h USING engrams e, spaces s
    WHERE h.engram_id = e.id AND e.space_id = s.id
      AND s.owner_person_id <> h.person_id
      AND NOT EXISTS (
          SELECT 1 FROM memberships m
          WHERE m.space_id = s.id AND m.person_id = h.person_id
      );
    PERFORM set_config('role', previous_role, true);
END
$cleanup$""",
    "DROP POLICY IF EXISTS memberships_self_delete ON memberships",
]


async def forwards() -> MigrationManager:
    """Replace the membership helper, grant its rights and erase old orphans.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Apply the fixed statements in one transaction."""
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
