-- Runs once, at first cluster bootstrap, as the image's default bootstrap
-- superuser ("postgres" -- see docker-compose.yml, which deliberately does
-- NOT set POSTGRES_USER to "reef").
--
-- The official postgres image always makes POSTGRES_USER a superuser with
-- BYPASSRLS, whatever its name, and Postgres refuses to ever strip that
-- attribute back off the bootstrap role (ALTER ROLE ... NOSUPERUSER on it
-- fails: "The bootstrap superuser must have the SUPERUSER attribute").
-- Superusers and BYPASSRLS roles ignore row security entirely, and FORCE
-- ROW LEVEL SECURITY does not change that -- it only extends RLS to the
-- table *owner*, a weaker guarantee. So the app role can never itself be
-- the bootstrap role: it has to be created fresh, ordinary, with neither
-- attribute, which is what this script does.
--
-- An earlier version of this comment claimed "Railway's managed Postgres
-- does not hand out superuser to the app role either". That was wrong, and
-- the error was expensive: Railway injects the same bootstrap superuser
-- credential into every service, so production ran as `postgres` and every
-- policy was inert from the first deploy until 7 Aug 2026. The tests were
-- honest -- they ran against this constrained role -- but they were proving
-- a shape production did not have.
--
-- Production now runs as `reef_app`, created by scripts/provision_app_role.py,
-- with the admin credential kept for migrations only. Keep this file and that
-- script in agreement: this is the shape both dev and production must have.
CREATE ROLE reef WITH LOGIN PASSWORD 'reef';
CREATE DATABASE reef OWNER reef;
CREATE DATABASE reef_test OWNER reef;

-- The owner of reef.rls's helper functions, and of nothing else. BYPASSRLS is
-- the point: FORCE ROW LEVEL SECURITY subjects even the table owner to
-- policies, so a policy on memberships whose predicate reads memberships
-- recurses until the stack is exhausted. A SECURITY DEFINER function owned by
-- a BYPASSRLS role breaks that cycle, and because this role cannot log in,
-- the bypass is reachable only by calling one of those functions.
--
-- NOLOGIN, no password: nothing ever connects as it. The grant lets "reef"
-- (which runs migrations here, as it owns the databases) execute
-- ALTER FUNCTION ... OWNER TO reef_authz -- Postgres requires the executing
-- role to be a member of the role it hands ownership to.
CREATE ROLE reef_authz NOLOGIN BYPASSRLS;
GRANT reef_authz TO reef;

-- CREATE on the schema is required of the *new* owner when a function's
-- ownership is reassigned, so without this every ALTER FUNCTION ... OWNER TO
-- fails with "permission denied for schema public". It is granted per
-- database, hence the reconnects. Handing CREATE to a role nothing can log in
-- as is not the widening it looks like: the privilege is exercisable only
-- through a SECURITY DEFINER function this repo wrote and owns.
\c reef
GRANT CREATE ON SCHEMA public TO reef_authz;
\c reef_test
GRANT CREATE ON SCHEMA public TO reef_authz;

-- A stand-in for production's reef_app, and the only way the test suite can
-- tell the truth about privileges.
--
-- "reef" OWNS the tables here, and an owner's privileges are implicit: REVOKE
-- UPDATE followed by GRANT UPDATE (version) does not bite an owner the way it
-- bites reef_app in production. A test asserting "a member cannot rewrite a
-- cove's slug" would therefore pass against "reef" for entirely the wrong
-- reason, and the policy could ship broken.
--
-- This repo has already paid for that mistake once: production ran as the
-- bootstrap superuser for five days while the tests -- honest in themselves,
-- but run against a constrained role -- proved a shape production did not
-- have. See the header of this file.
--
-- Ordinary, non-owner, no DDL, granted exactly what reef_app is granted. It
-- exists only in dev and test clusters; production has no equivalent and
-- needs none.
CREATE ROLE reef_probe WITH LOGIN PASSWORD 'probe' NOSUPERUSER NOBYPASSRLS
  NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT CONNECT ON DATABASE reef_test TO reef_probe;
GRANT USAGE ON SCHEMA public TO reef_probe;
REVOKE CREATE ON SCHEMA public FROM reef_probe;
