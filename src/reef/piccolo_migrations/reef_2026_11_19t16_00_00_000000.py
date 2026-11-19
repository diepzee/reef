"""Create the two avatar helpers production never got.

``reef_member_faces`` and ``reef_member_avatar`` were added on 13 August
2026 by a migration that took its SQL from :mod:`reef.rls` at run time. A
database built from scratch gets them under today's names. Production ran
that migration in August, under the names of the day, and the rename of
the security helpers afterwards (#99) did not carry these two. Since then
every member list has failed in production with ``function
reef_member_faces(unknown) does not exist``: the members panel, the
``list_coves`` tool and the warning ``prepare_to_share`` shows.

The SQL is fixed text, generated once from :func:`reef.rls.avatar_statements`
on 19 November 2026, not imported: an import is how this went wrong, and
the security code is about to be renamed again (RUG-40). ``CREATE OR
REPLACE`` makes it harmless on a database that already has them.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically

ID = "2026-11-19T16:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "create the avatar helpers missing in production"

#: Generated from ``reef.rls.avatar_statements()`` on 19 November 2026.
STATEMENTS = [
    "CREATE OR REPLACE FUNCTION reef_member_faces(p_cove uuid) RETURNS TABLE(person_id uuid, avatar_len int) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.id, octet_length(p.avatar_bytes)::int FROM persons p WHERE p.id IN (SELECT person_id FROM memberships WHERE cove_id = p_cove) AND p.avatar_bytes IS NOT NULL AND p_cove IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_member_faces(p_cove uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_member_faces(p_cove uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_cove uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_cove uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_cove uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_cove uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_cove uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_faces(p_cove uuid) TO rif_probe'; END IF; END $do$",
    "CREATE OR REPLACE FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) RETURNS TABLE(avatar_mime text, avatar_bytes bytea) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_catalog AS $reef$SELECT p.avatar_mime, p.avatar_bytes FROM persons p WHERE p.id = p_person AND p.id IN (SELECT person_id FROM memberships WHERE cove_id = p_cove) AND p.avatar_bytes IS NOT NULL AND p_cove IN (SELECT cove_id FROM memberships WHERE person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)$reef$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('ALTER FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) OWNER TO %I', authz); END $authz$",
    "REVOKE ALL ON FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) FROM PUBLIC",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON persons TO %I', authz); END $authz$",
    "DO $authz$ DECLARE authz text := COALESCE((SELECT rolname FROM pg_roles WHERE rolname = 'reef_authz'), (SELECT rolname FROM pg_roles WHERE rolname = 'rif_authz'), 'reef_authz'); BEGIN EXECUTE format('GRANT SELECT ON memberships TO %I', authz); END $authz$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) TO reef_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) TO reef'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) TO reef_probe'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_app') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) TO rif_app'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) TO rif'; END IF; END $do$",
    "DO $do$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rif_probe') THEN EXECUTE 'GRANT EXECUTE ON FUNCTION reef_member_avatar(p_cove uuid, p_person uuid) TO rif_probe'; END IF; END $do$",
]


async def forwards() -> MigrationManager:
    """Create ``reef_member_faces`` and ``reef_member_avatar``, or replace them.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically(STATEMENTS)

    manager.add_raw(run)
    return manager
