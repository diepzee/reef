# Restore

Durability for the Postgres store: two independent backup paths, and a
restore drill that proves the one thing that matters — the access model
survives. An untested backup is not a backup.

## Where backups live

**Railway's managed Postgres backups** — enable in the Railway dashboard
(Postgres service → Backups). These are Railway's own snapshot mechanism,
retained per Railway's plan-dependent policy (check the dashboard for the
current retention window). This is the first line of defense and needs no
code on our side.

**Independent `pg_dump` to R2** — `scripts/backup.py`, scheduled as a daily
Railway cron service running `uv run python scripts/backup.py`. This is a
second, out-of-band copy that does not depend on Railway's backup
infrastructure being correct, and that we can restore from without Railway
support. Dumps land in the `backups/` prefix of the R2 bucket named by
`RIF_S3_BUCKET`, as `backups/rif-<UTC timestamp>.dump`, in `pg_dump
--format=custom` form. R2 has no bucket-wide retention/lifecycle policy
configured for this prefix yet — treat that as a follow-up (a lifecycle rule
expiring objects after N days) once the backup has run unattended for a
while and the timestamp naming is confirmed to sort/list the way we expect.

## Required: the backup connection must bypass RLS

**This is not optional and not yet verified against Railway production —
see Phase 4 of [`runbook.md`](runbook.md).** `pages`, `revisions`, and
`attachments` all run
`FORCE ROW LEVEL SECURITY`, which — deliberately, per `docs/spec.md` and the
Task 2 migration — applies row security to the table *owner* too, not just
other roles. `pg_dump` issues `COPY <table> TO stdout` internally, and
Postgres refuses that outright on an RLS-protected table for any role that
is subject to the policy, superuser and BYPASSRLS roles excepted — it does
not silently dump zero rows, it errors:

```
pg_dump: error: query failed: ERROR:  query would be affected by row-level
security policy for table "attachments"
HINT:  To disable the policy for the table's owner, use ALTER TABLE NO
FORCE ROW LEVEL SECURITY.
```

Reproduced locally: `pg_dump` against the `rif` role (the app's own,
FORCE-RLS-bound role, matching how the app connects in production per
`docker/initdb/01-create-app-role-and-databases.sql`'s stated intent) fails
this way. `pg_dump` against the `postgres` bootstrap superuser succeeds and
produces a complete, restorable dump.

**Consequence:** `scripts/backup.py`'s `DATABASE_URL` must NOT be the same
connection string the `rif.server` app service runs with. The app's
connection is deliberately RLS-constrained — that is the entire point of
Task 2's design. The backup cron service therefore needs its own
`DATABASE_URL`, pointing at a role that bypasses RLS: either Railway's
managed Postgres admin/root credential (if Railway's plugin still exposes
one distinct from whatever role the app uses — unconfirmed, see Phase 4 of
[`runbook.md`](runbook.md)), or a dedicated role created with `BYPASSRLS` (not full
`SUPERUSER`) reserved for backups only, analogous to how local dev keeps
`postgres` (bootstrap, superuser) separate from `rif` (app, RLS-bound). A
role with plain `BYPASSRLS` is preferable to superuser for this: it is
enough to make `pg_dump` work and nothing more.

Locally this repo's `docker-compose.yml` already keeps these separate:
`postgres` (bootstrap superuser, used only for migrations/backups/admin)
vs. `rif` (the app's own role, created without `SUPERUSER` or `BYPASSRLS`
specifically so RLS is enforced against it the way it will be on Railway).
The backup cron's `DATABASE_URL` should be built the same way: a role
distinct from the app's.

## Restore invocation

```bash
# 1. Dump — must run as a role that bypasses RLS (see above). Locally:
docker compose exec db pg_dump --format=custom -U postgres -d rif > rif-<stamp>.dump
# In production this is scripts/backup.py, whose upload lands in R2 at
# backups/rif-<stamp>.dump — download that object first.

# 2. Target database, owned by rif — not created by rif. The rif role has
#    no CREATEDB privilege (deliberately: it is the RLS-constrained app
#    role, not an admin role), so this must run as the bypass-RLS role,
#    and the -O/OWNER matters: Postgres 15+ no longer grants CREATE on the
#    public schema to non-owners by default, so a database left owned by
#    whoever ran createdb (postgres) would make every CREATE TABLE in the
#    restore fail with "permission denied for schema public" once pg_restore
#    connects as rif.
docker compose exec db createdb -U postgres -O rif rif_restore

# 3. Restore — run through the same Postgres major version as the server
#    (here, via docker exec, so it uses postgres:17's own pg_restore). A
#    host-installed pg_restore from a different major version will refuse
#    the dump outright ("unsupported version ... in file header") — on a
#    Mac with Homebrew's default (non-versioned) postgresql formula this is
#    likely, since that formula tends to lag the server's major version;
#    install/use postgresql@17 specifically, or just run pg_restore inside
#    the container as below.
docker compose exec db pg_restore --clean --if-exists \
  -U rif -d rif_restore rif-<stamp>.dump

# 4. Verify — the numbers that actually matter
docker compose exec db psql -U postgres -d rif_restore -c \
  "select (select count(*) from pages) pages, \
          (select count(*) from revisions) revisions, \
          (select count(*) from memberships) memberships;"
```

Expected: counts matching the source database. **If `memberships` is zero
the restore is useless** — the access model, not the content, is the part
that must survive. A restore with pages but no memberships silently drops
the entire privacy boundary: every space would resolve to "no unique space
for this principal" and every read/write would deny, or worse, a bug
downstream could reintroduce access to everyone.

Also worth checking after any real restore: connect as the app's own role
(`rif` locally) with no `app.person_id` set and confirm `pages` reads back
zero rows — proof RLS is still armed on the restored database, not
accidentally dropped or left `NO FORCE` by the restore process.

### Local rehearsal performed for this task

Ran the exact sequence above, verbatim, against local Postgres only
(`docker compose`, port 5433) — no Railway, no R2, no production data.
Source `rif` database held 13 pages / 26 revisions / 4 memberships (from
the `import_mark.py` rehearsal, run twice to also prove re-imports don't
duplicate pages). `pg_dump -U postgres` succeeded; `pg_dump -U rif`
reproduced the RLS error above. `createdb -U rif` (no `-O`) reproduced
"permission denied to create database" — `rif` has no `CREATEDB`. A
host-side `pg_restore` (Homebrew's default `postgresql` formula, v14)
against the v17-format dump reproduced "unsupported version (1.16) in file
header" — hence running `pg_restore` through `docker compose exec` above,
which uses the container's own matching-version client. With those three
fixes applied, the documented commands restored cleanly into a scratch
`rif_restore` database: counts matched exactly (13 / 26 / 4), and a
post-restore RLS check (connecting as `rif` with no principal set)
returned zero rows, confirming FORCE RLS survived the restore.
`rif_restore` was dropped afterward — it was a rehearsal database, not a
fixture to keep around.

This proves the restore *mechanics* (the exact commands above) are correct.
It does **not** prove the backup cron works against Railway's actual
production role/credential setup — that depends on the still-unconfirmed
question in the previous section, and is a human step: Phase 4 of
[`runbook.md`](runbook.md).

**As of 7 Aug 2026 the R2 bucket does not exist and the backup cron has not
been created**, so `scripts/backup.py` has never run against production and
the drill has never been possible. There is real data in the store. Until a
dump is pulled from R2 and restored with matching counts, rif's only
durability is Railway's managed snapshots — one mechanism, unverified.

## Bucket locks cover attachment bytes — R2 has no versioning

Image bytes live in R2 as opaque-keyed objects (`attachments.object_key`,
never derived from `space_id` — see `src/rif/attachments.py`), outside
Postgres entirely. `pg_dump`/`pg_restore` only ever covers the metadata row
(`attachments` table: key, mime, size, description, status) — never the
bytes themselves.

**An earlier version of this document said to enable R2 object versioning.
R2 does not have it** — `GetBucketVersioning` and `PutBucketVersioning` are
both unimplemented. The equivalent is a **bucket lock**, which prevents
deletion and overwriting rather than letting you recover afterwards. See
Phase 4 of [`runbook.md`](runbook.md) for the two prefix-scoped rules to
set, and the warning about prefix-less rules being close to irreversible.

The gap this leaves is smaller than it looks. Versioning protects against
overwrite and delete; rif does neither. Every upload writes a fresh
`attachments/{uuid}-{hash}` key, and the MCP exposes no tool that deletes a
page or an object at all. Bytes can only be lost by something outside the
application — a hand-run CLI delete, or a leaked API token — and a lock
blocks both outright, which versioning would not have.

A restored Postgres database with attachment rows intact but a bucket with
no lock configured still has working object keys. Locks protect against
*loss*, not against restore working in the first place.

## The export mirror is a last resort, not a backup

Task 12's `python -m rif.export` writes one markdown file per page,
rendered with YAML frontmatter (`src/rif/export.py`, import-compatible
with `scripts/import_mark.py`'s `parse_markdown`). It is a manual,
run-by-hand exit hatch, not a backup path, and loses everything that makes
this system more than a folder of files:

- **Revisions** — only the current body of each page is exported; the full
  `revisions` history (every prior version, its message, its author) does
  not survive.
- **Attachments** — image bytes and their metadata rows are not exported
  at all.
- **Memberships** — the export is plain files on disk; there is no access
  model to restore into, because there is no RLS, no `spaces` table, no
  `memberships` table in the output. Re-importing an export starts from
  whatever the current seed topology is, not from a preserved history of
  who could see what.

Reach for it only when both the managed Railway backups and the R2 `pg_dump`
copies are gone or unreachable, and only as a way to get *content* back —
never as evidence the access model was preserved.
