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
-- attribute, which is what this script does. Railway's managed Postgres
-- does not hand out superuser to the app role either, so this makes local
-- dev/test match that shape rather than papering over it.
CREATE ROLE rif WITH LOGIN PASSWORD 'rif';
CREATE DATABASE rif OWNER rif;
CREATE DATABASE rif_test OWNER rif;
