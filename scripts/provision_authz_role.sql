-- Create the role that owns reef.rls's SECURITY DEFINER helper functions.
--
-- Run once per database, before the migration that installs those functions.
-- Idempotent: safe to re-run, and re-asserts the attributes rather than
-- assuming a role of the right name is the right shape.
--
--     railway run --service reef-app -- sh -c \
--       'psql "$REEF_MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 \
--          -f scripts/provision_authz_role.sql'
--
-- Requires superuser, which is why this is a manual step: the boot migration
-- deliberately runs as a non-superuser admin role, and BYPASSRLS cannot be
-- granted without one. scripts/provision_app_role.py does the same thing for
-- a fresh environment; this file exists for an already-provisioned database
-- where re-running that script would also reset the app role's password.
--
-- Why BYPASSRLS at all: a policy on memberships whose predicate reads
-- memberships is evaluated by running that same policy, recursively, until
-- the backend dies with "stack depth limit exceeded". FORCE ROW LEVEL
-- SECURITY closes the usual escape, because it subjects the table *owner* to
-- policies too -- so a SECURITY DEFINER function owned by the table owner
-- recurses identically. Only a BYPASSRLS owner breaks the cycle. Verified
-- both ways against a live server. See src/reef/rls.py's module docstring.
--
-- NOLOGIN and no password: nothing ever connects as this role, so the bypass
-- is reachable only by calling a function it owns -- and it owns exactly the
-- two this repo wrote.

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reef_authz') THEN
    ALTER ROLE reef_authz NOLOGIN BYPASSRLS;
    RAISE NOTICE 'reef_authz already existed; attributes re-asserted';
  ELSE
    CREATE ROLE reef_authz NOLOGIN BYPASSRLS;
    RAISE NOTICE 'reef_authz created';
  END IF;
END
$$;

-- Postgres requires the executing role to be a *member* of any role it hands
-- object ownership to, and the migration runs ALTER FUNCTION ... OWNER TO.
GRANT reef_authz TO CURRENT_USER;

-- Required of the *new* owner whenever a function's ownership is reassigned.
-- Without it every ALTER FUNCTION ... OWNER TO fails with "permission denied
-- for schema public". Not the widening it appears to be: a role nothing can
-- log in as can only exercise this through a definer function we own.
GRANT CREATE ON SCHEMA public TO reef_authz;

-- Fails loudly if the end state is wrong, rather than leaving the migration
-- to install policies that would recurse on the first request.
DO $$
DECLARE r record;
BEGIN
  SELECT rolcanlogin, rolbypassrls INTO r
    FROM pg_roles WHERE rolname = 'reef_authz';
  IF r.rolcanlogin OR NOT r.rolbypassrls THEN
    RAISE EXCEPTION 'reef_authz must be NOLOGIN and BYPASSRLS (login=%, bypass=%)',
      r.rolcanlogin, r.rolbypassrls;
  END IF;
  RAISE NOTICE 'verified: reef_authz is NOLOGIN + BYPASSRLS';
END
$$;
