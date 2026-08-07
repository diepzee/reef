-- Runs once, at first cluster bootstrap, as the image's default bootstrap
-- superuser ("postgres" -- see docker-compose.yml, which deliberately does
-- NOT set POSTGRES_USER to "rif").
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
-- Production now runs as `rif_app`, created by scripts/provision_app_role.py,
-- with the admin credential kept for migrations only. Keep this file and that
-- script in agreement: this is the shape both dev and production must have.
CREATE ROLE rif WITH LOGIN PASSWORD 'rif';
CREATE DATABASE rif OWNER rif;
CREATE DATABASE rif_test OWNER rif;
