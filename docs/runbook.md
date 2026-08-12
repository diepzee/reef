# rif — go-live runbook

rif went live on **6 Aug 2026**. This document is now two things: a record of
how it got there, and the list of what is still open. Each phase explains what
it does, why it exists, and what "done" looks like. The completed phases keep
their instructions rather than being deleted — they are what you would follow
to stand the service up again.

| Phase | State |
|---|---|
| 1 — Prove the connection works | **Done** on desktop, 6 Aug 2026. Phones untested |
| 2 — Who is allowed in | **Partly done** — one person seeded; nobody invited yet |
| 3 — Deploy the real service | **Done**, 6 Aug 2026 |
| 4 — Storage and safety nets | **Mostly done**, 7 Aug 2026 — R2 live, images working, one verified backup and a passed restore drill. The *schedule* is missing |
| 5 — Content: the import | **Done**, 6 Aug 2026 |
| 6 — Protocol and personas | **Redesigned 9 Aug 2026** — the protocol ships with the product (`src/rif/protocol.md`), no longer a page; `meta/persona.md` is still the smoke-test placeholder |
| 7 — Measure the context ceiling | **Open** |

## Open items

The single list. Grouped by what each one is waiting on, because that is what
determines when it can happen — not by priority.

**Waiting on the operator's Web frontend setup**

- [x] **Web frontend environment and redirect.** Done 2026-08-09. The working
      configuration was non-obvious — see "Web frontend → Setup" for the full
      picture. Short version: `WORKOS_CLIENT_ID` must be the client id of a
      **WorkOS Connect OAuth application** ("Rif Web", PKCE, managed-by-you),
      not the environment/default application's client id — the AuthKit
      domain's `/oauth2/authorize` only resolves Connect applications, and
      anything else 302s to `oauth2/error?error=application_not_found`. The
      redirect URI lives on that Connect app (Connect → Applications → Rif
      Web → Sign-in callback).

**Waiting on a dashboard** (Railway's CLI cannot do these — verified, not
assumed: it has no command for cron schedules or start commands)

- [ ] **Backup cron service.** Today's dump is a one-off. Phase 4, step 3 has
      the exact settings. *This is the highest-value item left.*
- [ ] **Railway managed Postgres backups.** The second, independent
      mechanism. Phase 4, step 2.
- [ ] **R2 bucket locks** — `attachments/` indefinite, `backups/` ~30 days.
      Phase 4, step 1. Do not create a prefix-less rule; the warning there
      explains why it is close to irreversible.

**Waiting on information or a decision**

- [ ] **Nathalie's invite.** rif is single-user until it goes out, and it is
      the deadline on everything privacy-related. Needs her exact email, then
      one `invite` call — no longer a migration. Phase 2.
- [ ] **`meta/persona.md`.** Still the 157-byte placeholder from the first
      smoke test, while every other page got real content. `mark.md` already
      holds much of what belongs in it. Phase 6, step 2.

**Waiting on a phone**

- [ ] **Connector on a phone**, then on her account and tier. Phase 1,
      steps 5–6. The mobile app is the whole reason for the remote-MCP design.
- [ ] **Context ceiling measurement.** Phase 7. Run it *after* the backup
      cron exists — step 1 pads the real corpus.

- [x] **`reefwith.me` cutover.** Done 2026-08-11. Domain live behind
      Cloudflare, WorkOS callback registered, `RIF_BASE_URL` flipped, and
      all three auth surfaces verified naming the same host. Every
      connector must be removed and re-added at `https://reefwith.me/mcp`.

**Known gaps, no external blocker**

- [ ] **`promotions` RLS is done, but check the pattern elsewhere.** The
      table shipped unprotected and was caught on 7 Aug. Worth a sweep for
      other tables added later without a policy.
- [ ] **Local dev database cannot migrate.** `rif` (local, port 5433) has the
      schema but zero rows in `migration`, so `scripts/migrate.py` tries to
      re-run the first migration and fails on `relation "persons" already
      exists`. Tests are unaffected — `conftest` builds `rif_test` directly.
- [ ] **R2 lifecycle rule for `backups/`.** Dumps accumulate forever
      otherwise. Must expire *later* than that prefix's bucket lock or the
      delete will not happen. See `docs/restore.md`.
- [ ] **Re-check the `postgresql-client-18` pin** whenever Railway upgrades
      its Postgres. `pg_dump` aborts against a newer server, so a server
      upgrade silently breaks every backup until the image catches up.

---

**Where the risk sits now.** The two that could lose data or leak it are
closed: RLS is enforced in production as of 7 Aug, and a backup has been
proven to restore with `memberships` intact. What remains is mostly
scheduling and content.

---

## Phase 1 — Prove the connection works

> **Done, 6 Aug 2026 — for his account, on desktop.** The connector is
> configured against the real service and answers tool calls from Claude
> Code. The endpoint was `https://rif-app-production.up.railway.app/mcp` at
> the time; since 11 Aug 2026 it is **`https://reefwith.me/mcp`** — configure
> new connectors against that one, and see "Custom domain: `reefwith.me`"
> for why the old URL cannot simply be left in place. Claude registered itself with no client id or secret entered
> anywhere, so Dynamic Client Registration works. Identity binding works too:
> `principal_from_claims` requires a verified email to bind an unknown
> subject, and it succeeded, so AuthKit's **access token does carry `email`
> and `email_verified`** — the open question flagged in `spike/NOTES.md`.
>
> **Desktop is confirmed (7 Aug 2026). Steps 5 and 6 are not:** the connector
> has not been tried on a phone, and her account and tier have not been tried
> at all. Step 6 is blocked behind Phase 2 regardless — an authenticated
> stranger is denied until she has been invited.
>
> The phone check still matters even though desktop works. The mobile app is
> the whole reason for the remote-MCP design, and mobile hosts are the ones
> that may truncate large tool results — see Phase 7.

### Why this came first

Her assistant is meant to live in the Claude mobile app. That plan rested on
two things nobody had tested:

- Claude's connector sign-in completes against WorkOS AuthKit. Claude
  registers itself as a client automatically, so the identity provider has to
  support Dynamic Client Registration. **Settled — it does.**
- A custom MCP connector works on her plan, on her phone. **Still open.**

The spike tests both with no real data at stake. It is a server with one
tool, `whoami`, which returns who you are. That is all it needs to do.

If the test fails, nothing you have built is wasted. The store, the tools and
the tests all survive. Only the choice of surface reopens, and the fallback is
a web app instead of the Claude app.

### Step 1 — Create the WorkOS account

WorkOS runs the login page and tells your server who signed in. It is free up
to a million users a month. You need two.

1. Sign up at workos.com and create an application.
2. Turn on AuthKit. Copy the domain it shows you. It looks like
   `https://something.authkit.app`.
3. Open **Applications → Configuration** and turn on **Dynamic Client
   Registration**. This is the setting the whole test depends on.

**Done when:** you have the AuthKit domain written down and Dynamic Client
Registration is on.

### Step 2 — Deploy the spike

Railway builds the `Dockerfile`, which starts the real server by default. The
spike ships in the same image, so you point one throwaway service at it by
overriding the start command.

```bash
cd ~/Repositories/haai/rif
railway init --name rif-spike
railway up
railway domain
```

Copy the domain Railway gives you. Then set the two variables the spike needs
and tell it to run the spike instead of the real server:

```bash
railway variables \
  --set WORKOS_AUTHKIT_DOMAIN=<your-authkit-domain> \
  --set RIF_BASE_URL=https://<your-railway-domain>
```

In the Railway dashboard, open the service, go to **Settings → Deploy**, and
set the start command to:

```
uv run python spike/server.py
```

Then redeploy:

```bash
railway up
```

**Done when:** the deploy logs show the FastMCP banner and a line reading
`AuthKit tokens will be validated against aud=https://<your-domain>/mcp`.

### Step 3 — Set the Resource Indicator in WorkOS

The log line above is an instruction, not a status. AuthKit has to stamp its
tokens with an audience that matches what your server checks, or every call
will be rejected.

Go back to the WorkOS dashboard, turn on **Resource Indicators**, and add
`https://<your-railway-domain>/mcp` as a resource. Use the exact URL from the
log line.

**Done when:** the resource is saved and matches the log line character for
character.

### Step 4 — Connect from your own account

1. Open claude.ai in a browser.
2. Go to **Settings → Connectors** and add `https://<your-railway-domain>/mcp`.
3. You should be sent to an AuthKit login page. Sign in.
4. In a conversation, ask Claude to call the `whoami` tool.

**Done when:** your email address appears in what `whoami` returns.

**Look closely at that output.** Write down exactly which fields come back.
The real server needs both `email` and `email_verified` to match you to your
row. If either is missing, stop and say so — the fix is either a WorkOS
setting or a different FastMCP provider, and it is much cheaper to find out
here than in Phase 3.

### Step 5 — Connect from your phone

Open the Claude mobile app on your phone. Check the connector is there and
`whoami` still works.

**Done when:** `whoami` returns your email on the phone, not just the browser.

### Step 6 — Connect from her account, on her phone

This is the real test. Her plan, her device.

1. On her account, add the same connector.
2. She signs up through the AuthKit page. Use the email she will keep — it is
   how the real server will recognise her later.
3. Ask for `whoami`.

**Done when:** her email comes back on her phone.

### Step 7 — Write down what you learned

`spike/NOTES.md` now records what was observed: the WorkOS dashboard's real
layout (DCR lives under Connect → Configuration → MCP Auth, and ships
disabled), the corrected route table, that Claude registers itself with no
manual client id, and that `email` / `email_verified` do arrive in the access
token.

One gap left deliberately: **the field-by-field `claims` dict was never
captured.** The spike's `whoami` output went unrecorded before the real
server took over, so what is known about the claims is inferred from binding
succeeding. If that ever matters, log the dict once from the real server
rather than re-deploying the spike.

Still to fill in, after Step 4 above: any limit hit on her plan.

### If Step 6 fails

Her surface reopens; nothing else does. Everything in `src/rif/` survives
untouched, and his own connector keeps working over the same deploy — the
fallback is a web app for her, not a redesign.

---

## Phase 2 — Who is allowed in

**Why this matters:** WorkOS authenticates anyone who signs up. Something else
has to turn "authenticated" into "allowed", and that something is now the
`invite` tool, not a hand-edited migration. Inviting an email address creates
the person's row (if new) and their membership row *before* they have ever
signed in; an authenticated stranger nobody invited is denied by
`principal_from_claims`, and RLS denies them rows even if that check were
bypassed.

**Already done.** The seed migrations create one person — Wouter — his personal
space, and the `household` shared space he owns (slug `school` until the 9 Aug 2026 cleanup renamed it). You can deploy and use the system
solo today.

### Bringing anyone else in

As the space's owner, from any connected assistant:

```
invite(space="household", email="<their-email>", display_name="<their-name>")
```

Then the person signs in to AuthKit with that exact address. On that first
sign-in `rif` binds their provider subject, creates their own personal space,
and seeds `meta/persona.md` there for them. Nothing else is needed — no
migration, no restart. (The operating protocol is not seeded: since 9 Aug
2026 it ships with the product in `src/rif/protocol.md`, versioned with the
code so improvements reach every member on deploy. `update_meta_page`
accepts only `meta/persona.md`; the web UI hides `meta/*` from listings.
One-time cleanup after that deploy: the now-dead `meta/protocol.md` rows in
Wouter's personal space and the shared space were deleted, and the shared
space's stale `school` slug was renamed to `household` — backup first, via
`RIF_MIGRATION_DATABASE_URL`.)

Two things to get right:

- **The address must match exactly**, including which Gmail if they have more
  than one, and the provider must report it verified. `rif` binds to the
  provider's subject on first login, so changing their account afterwards means
  clearing their `subject` column by hand.
- **Say out loud what an invite grants** before you call it. The invitee will
  permanently see everything in that space, past and future; the tool returns a
  disclosure line with today's page count for exactly this reason. Removal
  (`remove_member`) stops future access — it cannot unshare what was read.

If you typo an address, `remove_member` on the invite you never got to use also
erases the orphaned person row, so the mistake leaves nothing behind.

---

## Phase 3 — Deploy the real service

> **Done, 6 Aug 2026.** The real service runs at
> `rif-app-production.up.railway.app` — since 11 Aug 2026 also, and by
> preference, at `reefwith.me` — and step 3's check passes: `list_spaces`
> returned exactly `personal` and the shared space (then aliased `household`,
> briefly the slug `school`, renamed back to `household` on 9 Aug 2026), with no internal space identifiers
> and nothing belonging to anyone else.

**Why it's safe now:** the server *refuses to boot* HTTP without auth
configured (a final-review fix — misconfiguration is a crash, not an open
endpoint), and the store is empty anyway until Phase 5.

**How deploys actually happen.** Railway has `main` connected to the
production environment with *"Auto deploys when pushed to GitHub"* enabled.
**Merging to `main` ships to production.** There is no CI in this repo — no
`.github/workflows`, no tests gating the merge — so the merge button is the
deploy button, and nothing checks the build first.

Two consequences worth internalising:

- A pull request that looks harmless (a docs tweak, a copy change) still
  rebuilds and redeploys the live service. Check that it *builds*, not just
  that the diff reads well.
- The setting lives in the Railway dashboard, not in this repo, so nothing in
  the codebase reveals it. Railway → `rif-app` → Settings → Source shows the
  connected branch and lets you disconnect or disable it.

The `railway up` commands throughout this runbook are the *manual* path —
they upload your working directory directly, bypassing git. Useful for the
spike service and for recovering when a bad commit is already on `main`.

1. ```bash
   railway add --database postgres
   railway up
   ```
   The Dockerfile runs `scripts/migrate.py` on boot (advisory-locked), which
   applies the schema, RLS policies, and your Phase 2 seed.
2. **Verify the shared-space owner the multi-user-spaces migration backfilled**
   (`rif_2026_08_08t10_00_00_000000`). For a space that predates ownership it
   assigns `owner_person_id` to the member with the lowest person id — an
   arbitrary pick, not necessarily the right one. Check it:

   ```sql
   SELECT s.slug, p.email
   FROM spaces s JOIN persons p ON p.id = s.owner_person_id
   WHERE s.kind = 'shared';
   ```

   If the wrong person came out owner, reassign before anyone relies on
   owner-only tools (`invite`, `remove_member`):

   ```sql
   UPDATE spaces SET owner_person_id = (SELECT id FROM persons WHERE email = '<the-owner>')
   WHERE slug = '<the-space>';
   ```
3. **Swap the connector** on both accounts from the spike URL to the real
   `/mcp` URL (same domain if you reused the service — then nothing to swap).
4. **Verify, on each phone:** `list_spaces` returns that person's `personal`
   space plus every shared space they belong to, with each space's member list
   — never a space they were never invited to, never anyone else's `personal`
   space (addressing is `personal` or a slug, and membership decides the rest;
   that's a tested invariant, but see it once with your own eyes).
5. **Verify the lock:** `curl -i https://<domain>/mcp` from anywhere →
   rejected, not a tool listing.
6. Commit ticking Task 6's deploy checkboxes — the code commit deliberately
   didn't claim the deploy was live.

---

## Phase 4 — Storage and safety nets

> **Open — and now the priority. Nothing in this phase has been done**
> (confirmed 7 Aug 2026): no R2 bucket, no backup cron. Three consequences,
> all live right now:
>
> 1. **The only copy of the corpus is Railway's managed Postgres backup.**
>    Phases 3 and 5 put real, expensive-to-reconstruct content into
>    production — a health page compiled from a full dossier, a character
>    portrait, work and finance pages — and none of it is in git.
> 2. **`scripts/backup.py` cannot run at all.** It streams `pg_dump` straight
>    to R2, so the independent copy does not exist even in principle until
>    the bucket does. Do the R2 half first; the backup half depends on it.
> 3. **`add_image` and `read_image` are broken in production.** Both build an
>    `S3ObjectStore` per call (`src/rif/server.py:441`, `:462`), and with the
>    S3 settings empty that constructor raises `ValueError: Invalid
>    endpoint:` from boto3. The server boots regardless — unlike auth, which
>    deliberately refuses to boot when misconfigured, storage fails only when
>    a tool is called, and does so with an error that does not name its
>    cause. Worth a clearer failure message when this phase is done.

**R2 (images):**

1. Cloudflare dashboard → R2 → create bucket `rif`.

   **Not object versioning — R2 has none.** `GetBucketVersioning` and
   `PutBucketVersioning` are both in R2's unimplemented-operations table. An
   earlier draft of this runbook said to enable it; there is no such setting
   to find. The feature R2 does have is **bucket locks**, which prevent
   deletion and overwriting for a fixed period or indefinitely.

   Set two rules, scoped by prefix — they need opposite policies:

   | Prefix | Rule | Why |
   |---|---|---|
   | `attachments/` | lock indefinitely | Image bytes are never in `pg_dump`, and rif itself never deletes or overwrites an object — every upload takes a fresh key and no tool deletes. The lock guards against a stray CLI delete or a leaked token, which is the only way they can go. |
   | `backups/` | lock ~30 days | Protects recent dumps from the same threats while still letting old ones age out. |

   **Do not set a rule without a prefix.** Cloudflare's docs are explicit
   that such a rule covers every object in the bucket, that lock rules beat
   lifecycle rules, and that *a bucket cannot be emptied while lock rules
   remain configured*. Deleting the policy afterwards does not release
   objects already inside their retention window. A bucket-wide indefinite
   lock would therefore trap every daily dump forever, and the mistake is
   effectively irreversible.

   Any lifecycle expiry for `backups/` must be **longer** than that prefix's
   lock, or the delete simply will not happen.
2. Create an S3-compatible API token, then:

   ```bash
   railway variables --set RIF_S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com \
     --set RIF_S3_BUCKET=rif --set RIF_S3_ACCESS_KEY=<key> --set RIF_S3_SECRET_KEY=<secret>
   railway up
   ```

**Backups — read this part, it has a trap:**

`FORCE ROW LEVEL SECURITY` means even the table owner sees zero content rows
without a principal set — and `pg_dump` connects with no principal. **A backup
run as the app role fails outright** (`query would be affected by row-level
security policy`); it does not silently dump zero rows. Reproduced locally,
not theorized. The backup connection needs a role with `BYPASSRLS`.

**The credential already exists.** Since 7 Aug 2026 the roles are split:
`DATABASE_URL` is the constrained `rif_app`, and `RIF_MIGRATION_DATABASE_URL`
is the admin role. Give the backup cron the latter. Before that date the app
itself ran as the superuser, so this trap could not fire — and neither could
RLS.

### The privileged-act trail (12 Aug 2026)

Four operations reach past the row policies, inside `SECURITY DEFINER`
functions owned by `rif_authz`: minting an invitation, admitting a member,
removing one, transferring a cove, and erasing an account. No row policy can
express those without permitting a great deal more, so what they carry is
**accountability, not prevention** — and `src/rif/audit.py` is the
accountability half.

Each one emits a Logfire record naming the actor, the cove, and the effect.
**Identifiers only** — no address, no display name, no page path, no body. A
trail carrying those would be a second copy of the corpus in a third party's
database, which is the exposure the row-level-security work exists to shrink.

To read it, filter `service_name = rif` and `action` starting `invite.` or
`cove.` or `account.`. To answer "what happened to my invitation", the actor
and invitee ids are there; the address is deliberately not, and has to come
from the database.

It is inert without `LOGFIRE_TOKEN`, like the rest of telemetry — an audit
trail that can refuse an account deletion is worse than one that occasionally
misses an entry. That is a real limitation, not a hedge: records are best
effort, and losing telemetry loses the trail for that period.

**What this does not do.** It does not make reef secure from whoever can
deploy it. A build can be shipped that does not call `audit.record` at all.
The property bought is narrower and still worth having: administrative acts
leave a mark outside the database, promptly, on a service the application
cannot rewrite — so erasing them takes a second and different kind of access.

### A third role: `rif_authz` (12 Aug 2026)

There is now a **third** role, and it is not a credential — nothing can log in
as it. `rif_authz` owns the `SECURITY DEFINER` helper functions that every RLS
policy calls (`rif_space_ids()`, `rif_member_space_ids()`), and it holds
`BYPASSRLS` because nothing else can.

The reason is worth keeping, because it is not obvious and it killed two
earlier designs: a policy on `memberships` whose predicate reads `memberships`
is evaluated by running that same policy, forever — the server dies with
*stack depth limit exceeded*. `FORCE ROW LEVEL SECURITY` closes the usual
escape hatch, since it subjects the table **owner** to policies too, so a
definer function owned by the table owner recurses just the same. Only a
`BYPASSRLS` owner breaks the cycle. Verified against a live server both ways
before it was relied on.

**Provisioning is a one-time manual step**, because creating a `BYPASSRLS`
role needs superuser and the boot migration deliberately runs as the
non-superuser admin role.

**On an existing database** (which production is), run the standalone SQL —
it touches nothing but this role:

```bash
railway link          # interactive: pick rif / production / rif-app
railway run --service rif-app -- sh -c \
  'psql "$RIF_MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 \
     -f scripts/provision_authz_role.sql'
```

`railway run` injects the service's own variables, so the credential never
leaves Railway — no copying a DSN anywhere. The script is idempotent,
re-asserts the attributes if the role already exists, and raises if the end
state is wrong.

**Do not** reach for `scripts/provision_app_role.py` here. It does the same
thing plus more, but it also runs `ALTER ROLE rif_app ... PASSWORD`, so
running it with a fresh password rotates the credential `DATABASE_URL` still
holds and the app stops being able to connect. That script is for standing up
a *new* environment.

Either path grants `rif_authz` to the migration role (Postgres requires the
executing role to be a *member* of a role it hands function ownership to) and
grants it `CREATE ON SCHEMA public` (required of the **new** owner whenever a
function's ownership is reassigned — without it every
`ALTER FUNCTION ... OWNER TO` fails with *permission denied for schema
public*).

**The migration refuses to run if the role is missing or lacks `BYPASSRLS`**,
rather than installing policies that would recurse on the first request. If a
deploy fails with that error, run the provisioning script and redeploy.

Granting `CREATE` to `rif_authz` is not the widening it looks like: the role
cannot log in, so the privilege is reachable only through a definer function
this repo wrote and owns.

**Two traps found the first time this was run for real (7 Aug 2026), either
of which alone meant no backup:**

- **`pg_dump` must be at least the server's major version.** Railway's
  Postgres is **18.4**; Debian trixie's default `postgresql-client` is 17.x,
  and `pg_dump` aborts outright against a newer server. The `Dockerfile` now
  installs `postgresql-client-18` from PGDG. **Re-check this pin whenever
  Railway upgrades the server** — the failure is a hard abort, not a
  degraded dump.
- **The credential must not be `DATABASE_URL`.** That is the constrained
  `rif_app` role, and `pg_dump` fails against it. `scripts/backup.py` reads
  `RIF_BACKUP_DATABASE_URL` or `RIF_MIGRATION_DATABASE_URL` and refuses to
  start without one.

**Status: a real backup exists and the drill has passed.**

1. ✅ The backup credential is `RIF_MIGRATION_DATABASE_URL`, already set on
   the service and distinct from the app's `DATABASE_URL`.
2. Enable Railway's **managed Postgres backups** in the dashboard (belt).
   Still to do.
3. **Schedule the cron service** (braces). Still to do — Railway's CLI cannot
   set a cron schedule, and a root `railway.json` must **not** be used, since
   `rif-app` would pick it up and turn the live server into a cron job. In the
   dashboard: **New Service → deploy from this repo**, then Settings →
   - Start command: `uv run python scripts/backup.py`
   - Cron schedule: `0 3 * * *`
   - Variables: `RIF_MIGRATION_DATABASE_URL`, `RIF_S3_ENDPOINT`,
     `RIF_S3_BUCKET`, `RIF_S3_ACCESS_KEY`, `RIF_S3_SECRET_KEY`

   It needs no `PORT` — and must not have one, or `rif.server` would boot a
   second instance.
4. ✅ **The drill — passed 7 Aug 2026.** One real backup taken
   (`backups/rif-20260807T140148Z.dump`, 195,004 bytes), downloaded from R2,
   restored into a scratch `postgres:18` container, counts compared against
   production:

   | | production | restored |
   |---|---|---|
   | pages | 22 | 22 |
   | revisions | 26 | 26 |
   | **memberships** | **2** | **2** |
   | persons / spaces | 1 / 2 | 1 / 2 |

   RLS survived the restore: connecting as `rif_app` with no principal
   returned zero pages, and `FORCE` was still set on `pages`, `revisions`,
   `attachments` and `promotions`. If `memberships` is ever 0, the backup is
   decorative — an untested backup is not a backup.

   Note the drill needs a **matching major version** locally too: a v17
   `pg_restore` cannot read an 18-format dump.

---

## Phase 5 — Content: the import

> **Done, 6 Aug 2026.** The store holds 16 personal pages and 5 in the shared
> space (now slugged `household`; called `school` at the time).
 The final disposition differs from the sketch in step 2 below: the
> shared layer came out as `house.md`, `future-home.md` and `travel.md`
> rather than the `money.md` / `family-film.md` split proposed here, and
> `health.md` and `finances.md` both stayed personal.
>
> **Step 4's cross-check has not happened** — verifying from her phone that
> the shared space's pages are visible and personal ones are not needs an
> invited second person first.
> That check is the moment the privacy design faces reality, and it is still
> outstanding.

**Why the ceremony:** this is the one step that moves your actual life into
the system, and the disposition (what's shared vs private) is a set of
judgment calls only you can make. The importer takes explicit filename lists —
nothing moves by inference.

1. **Finalize the disposition** in `mark/meta/architecture.md` (branch
   `forest-leech`). Two calls are still open, flagged since the first draft:
   - `health.md` — private by default, but this is the page where the default
     deserves an actual decision (a shared summary is possible without moving
     the investigation notes).
   - `finances.md` — the proposed split sends joint account/2034 plan/family
     loan to the shared space and keeps pension+insurance private (it
     references the
     cardiac story).
2. **Prepare the split files** by hand in a scratch directory: `money.md`,
   `family-film.md`, trimmed `finances.md`/`film-taste.md`. Check
   `scripts/import_mark.py`'s `HOUSEHOLD`/`PERSONAL` lists match your final
   disposition — and confirm the files it doesn't list are intentionally
   excluded.
3. **Import against production:**

   ```bash
   railway run uv run python scripts/import_mark.py <scratch-dir> wouter@rugvin.be
   ```

4. **Verify from both sides:** your phone — `load_all_context` shows
   everything; her phone — the shared space's pages visible, none of yours.
   This is the
   moment the privacy design faces reality; look at it directly.

---

## Phase 6 — Protocol and personas

> **Step 1 done, 6 Aug 2026** — but written in the shared space, which no
> longer works: `update_meta_page` now refuses any space but `personal`, and
> `get_operating_protocol` never read the shared copy in the first place.
> **Rewrite it in your personal space.** **Step 2 is not done either:**
> `meta/persona.md` is still the 157-byte placeholder from the first end-to-end
> test, while every other page got real content. It is the page that steers the
> assistant's voice, and `mark.md` in the personal space already holds much of
> what belongs in it. Step 3 waits on Phase 2.

The code ships a built-in fallback protocol, but the real thing is content:

1. **The protocol lives in each person's own personal space, not a shared one.**
   `get_operating_protocol` reads `meta/protocol.md` only from the personal
   space it is called for, never from `household` or any other shared space — the
   protocol and the persona are per-person. Anyone invited after go-live gets
   both pages created for them at first sign-in, seeded from the built-in
   template. You were seeded by migration rather than by invite, so write your
   own: `update_meta_page` in your **personal** space (or ask any connected
   assistant) with the operating rules — load context first, compile don't
   dump, private-by-default routing, promote-only-with-consent — plus the
   **onboarding behavior**: an assistant meeting an empty personal space
   introduces itself, asks what to call itself, and interviews gently to seed
   the persona.
2. Write your own `meta/persona.md`, also in your **personal** space (Mark's
   tone lives in `mark/wiki/mark.md` today — port it).
3. **Hers is not yours to write.** Her first conversation, via the onboarding
   behavior, fills in the `meta/persona.md` waiting in her own personal space.
   She names her own assistant.

---

## Phase 7 — Measure the context ceiling

> **Open.** Less urgent than it looks: `load_index` is the primary retrieval
> path and carries no bodies, so ordinary use no longer pushes against the
> ceiling. It is `load_all_context` — the maintenance path — that needs a
> measured budget. Note that step 1 pads the *real* corpus, so run it after
> Phase 4's backup is proven, not before.

**Why:** the server counts body characters; the client cares about serialized
tokens, and the mobile host may truncate big tool results on its own. The
budget must come from measurement on the real device, not arithmetic.

1. `railway run uv run python scripts/measure_context.py wouter@rugvin.be <size>`
   to pad the corpus to ~60KB, then ~200KB, then ~500KB.
2. After each: from the Claude mobile app, ask the assistant to
   `load_all_context` and report `page_count`, `included_count`, and whether
   every non-null body arrived intact (the payload is built so a host-side cut
   is detectable — a mismatch means truncation in transit).
3. Record results in `docs/superpowers/plans/context-limits.md`; set
   `RIF_CONTEXT_CHAR_BUDGET` on Railway comfortably below the first size that
   misbehaved; `railway up`.
4. Clean up: `scripts/measure_context.py wouter@rugvin.be 0`.

---

## Web frontend

A browser UI ships in the same Docker image, served at `/app` by the same
service. Members can browse and edit pages; owners can create and manage
spaces. Built with React and Bun, compiled to `frontend/dist` during the
image build. The frontend redirects unauthenticated requests to
`/api/auth/login`, which requires two environment variables and a redirect
URI configuration.

**Setup** (done 2026-08-09; kept for rebuild-from-scratch):

The WorkOS account layout, established the hard way: everything lives in the
WorkOS **Production** environment of "Haai's Project" — the AuthKit domain
`thankful-origami-62.authkit.app`, the user records, and the Claude
connector's DCR-registered clients. The **Staging** environment is empty and
unused (0 users; its similarly-named client ids are a trap). Sign-in method
in Production is Google OAuth; Email+Password is disabled.

The AuthKit domain's `/oauth2/*` endpoints are WorkOS **Connect** (the OAuth
server MCP clients register against via DCR). Its `/oauth2/authorize` only
resolves **Connect applications** — the environment/default application's
client id is not one, and using it 302s to
`oauth2/error?error=application_not_found`. So the browser login needs its
own static Connect app:

1. **Connect application.** WorkOS → Production → Connect → Applications →
   Create application → OAuth application, "Managed by you" (no consent
   screen), **Use PKCE** checked (rif's web login is a public client — no
   secret). Ours is "Rif Web", `client_01KZK71Z2R7PYWWS9WBNG9BEBD`.
2. **Redirect URI.** On that Connect app → Sign-in callback → add
   `{RIF_BASE_URL}/api/auth/callback`.
3. **Environment variables:**
   - `WORKOS_CLIENT_ID` — the Connect app's client id (see above).
   - `RIF_SESSION_SECRET` — a 64-character hex string, generated with:
     ```bash
     python -c "import secrets; print(secrets.token_hex(32))"
     ```
   Until these are set, `/api/auth/login` returns 503 and the MCP surface
   is unaffected. The MCP path never reads `WORKOS_CLIENT_ID` (only
   `WORKOS_AUTHKIT_DOMAIN` + `RIF_BASE_URL`), so changing it cannot break
   connectors.
4. **Development caveat.** `RIF_DEV_INSECURE=1` disables session encryption,
   for local testing only. Never set it in production.

**Sign-out:** clearing rif's own cookie is not enough — the SPA bounces any
unauthenticated visit into `/api/auth/login`, and a live AuthKit session
silently re-issues a code, signing the user straight back in. So the
callback stores the access token's `sid` claim in the sealed session, and
`POST /api/auth/logout` returns a `logout_url`
(`https://api.workos.com/user_management/sessions/logout?session_id=…&return_to=…/app/signed-out`)
that the frontend navigates to, ending the AuthKit session too, before
landing on `/app/signed-out`. Sessions sealed before this change carry no
sid; those sign out locally and land on `/app/signed-out` directly. If the
WorkOS hop ever refuses the `return_to`, configure the sign-out /
homepage URI on the WorkOS side to allow it.

**Custom domain: `reefwith.me`**

The branded domain serves the marketing page at `/`, the app at `/app`,
and MCP at `/mcp` — the same service as the railway.app hostname, which
keeps answering. The zone lives on Cloudflare under the `wouter@rugvin.be`
account; the Railway project lives under the same account, so
`railway login` must be that identity, not `wouter@ringtime.ai`.

Order matters — the base URL drives the OAuth audience, so flip it last.

*Done (2026-08-11):*

1. Railway → `rif-app` → custom domain `reefwith.me`. Railway detects the
   Cloudflare CDN in front and verifies ownership via a TXT record rather
   than an ACME challenge, so **the orange cloud can stay on** — no need
   for the DNS-only step other Railway guides describe.
2. Cloudflare DNS for `reefwith.me`:
   - `CNAME @ → 8dw8uxk0.up.railway.app`, proxied. Cloudflare flattens the
     apex CNAME, so `dig reefwith.me` still answers with Cloudflare A
     records — that is expected, not a misconfiguration.
   - `TXT _railway-verify → railway-verify=<token from Railway>`.
   - `CNAME www → reefwith.me`, proxied, plus a redirect rule
     ("Redirect from WWW to root", wildcard `https://www.*` → `https://${1}`,
     301, preserve query string). Railway does not redirect, it would just
     serve the app on both names, so the redirect belongs at the edge.
   - The two apex `A` records the registrar had parked there
     (`54.149.79.189`, `34.216.117.25`) were deleted. While they were
     live Cloudflare returned **522** after ~40s.
3. SSL/TLS encryption mode → **Full (strict)**. Safe because Railway
   serves a real Let's Encrypt cert for `reefwith.me` at the origin;
   verify before switching with:
   ```bash
   RIP=$(dig +short 8dw8uxk0.up.railway.app | head -1)
   curl -sSIv --resolve "reefwith.me:443:$RIP" https://reefwith.me/
   ```
   "Flexible" would cause a redirect loop; plain "Full" leaves the origin
   hop unvalidated.

4. WorkOS dashboard → Production → Connect → Applications → Rif Web →
   Sign-in callback → added `https://reefwith.me/api/auth/callback`. The
   railway.app callback is still listed and still marked **Default**;
   retiring it later means reassigning Default first, since WorkOS
   requires one.
5. `RIF_BASE_URL=https://reefwith.me`. From here the MCP resource is
   advertised under the new domain and access tokens are audience-bound
   to `{RIF_BASE_URL}/mcp`, so connectors added against the old URL must
   be removed and re-added at `https://reefwith.me/mcp` — they do not
   heal on their own. Both hostnames keep answering; the old one can be
   removed once every member has switched.

6. **WorkOS → Connect → Configuration → MCP resource indicators → add
   `https://reefwith.me/mcp`.** Easy to miss and nothing else reveals it.
   This is an allowlist of the `resource` values (RFC 8707) AuthKit will
   mint tokens for. It is *not* the same list as the Connect app's
   redirect URIs, and changing `RIF_BASE_URL` does not update it. Keep
   the old entry alongside the new one so connectors still on the
   railway.app URL keep working through the transition.

*Verifying the auth surface after a base-URL change:*

```bash
curl -sSI https://reefwith.me/mcp | grep -i www-authenticate
curl -sS https://reefwith.me/.well-known/oauth-protected-resource/mcp
curl -sSI https://reefwith.me/api/auth/login | grep -i ^location
```

All three must name the same host. Note the metadata path carries the
`/mcp` suffix — plain `/.well-known/oauth-protected-resource` returns
"Not Found" and proves nothing.

*Two connector failures that look alike and are not:*

- **Registers, never prompts for login, sits "unauthorized".** Origin
  mismatch: the client connected to one host and the metadata claimed
  another, so under RFC 9728 it refuses to begin authorization. Fix
  `RIF_BASE_URL`, then delete and re-add the connector — a registration
  bound to the old resource does not heal.
- **Prompts, then fails after sign-in.** The callback carries
  `?error=invalid_target` and the client reports a confusing downstream
  error (Claude says `state: Field required`, because the callback has an
  error instead of `code`+`state`). This is step 6 above: AuthKit was
  asked for a `resource` it has no indicator for. The client-side error
  names neither WorkOS nor the resource, so read the callback URL — the
  `error=` parameter is the only honest signal.

When reading a browser request to diagnose this, **copy the URL only.**
"Copy as cURL" carries live `sessionKey` cookies.

*Diagnosing this hop:* the failure mode tells you which side is broken.
**522** = Cloudflare cannot reach the origin (DNS points somewhere wrong).
**404 with `x-railway-fallback: true`** and body `Application not found` =
Cloudflare reaches Railway, but Railway has not finished verifying the
domain — it took ~100s after the DNS change. **526** = origin cert invalid
under Full (strict); on `www` this also appears for the ~1 min before a
new redirect rule propagates, because unmatched requests fall through to
the origin under a hostname Railway has no certificate for.

---

## Reference

**Env vars (Railway):**

| Var | Purpose |
|---|---|
| `WORKOS_AUTHKIT_DOMAIN` | AuthKit app domain (`thankful-origami-62.authkit.app`, owned by the WorkOS **Production** environment); auth refuses to boot without it |
| `WORKOS_CLIENT_ID` | Client id of the "Rif Web" **Connect OAuth application** (browser login only; the MCP path never reads it). Not the environment/default application client id — that one gets `application_not_found` |
| `RIF_SESSION_SECRET` | 64-char hex; seals the browser session + OAuth state cookies |
| `RIF_BASE_URL` | Public root URL, no path; drives advertised resource URL + token audience |
| `DATABASE_URL` | The **constrained** `rif_app` role — no DDL, subject to RLS. Do not point this back at Railway's injected `${{Postgres.DATABASE_URL}}`: that is the superuser, and it turns every policy off |
| `RIF_MIGRATION_DATABASE_URL` | The admin role. Used by `scripts/migrate.py` for DDL on boot, and by the backup cron for `pg_dump`. Never read by the server — and since the `env -u` scrub in the Dockerfile CMD, not even *present* in the server's environment: the boot shell execs the server through `env -u`, so after migration finishes no process in the container holds this credential (`/proc/*/environ` included). The variable still lives on the Railway service — that is what re-arms the next boot and what the restore runbook reads via the control plane |
| `RIF_S3_ENDPOINT` / `RIF_S3_BUCKET` / `RIF_S3_ACCESS_KEY` / `RIF_S3_SECRET_KEY` | R2 for images + backups |
| `RIF_CONTEXT_CHAR_BUDGET` | Set from Phase 7 measurement |
| `LOGFIRE_TOKEN` | Write token for the `wouterdurnez/reef` Logfire project (EU instance). **Optional** — unset means telemetry is inert, and a missing token can never fail a request or stop the server booting |
| `LOGFIRE_BASE_URL` | Only if the Logfire instance moves. Defaults to `https://logfire-eu.pydantic.dev`; the SDK's own default is the US instance, which is the wrong one for this project |
| `PORT` | Set by Railway; presence = HTTP mode = auth required |

**Escape hatches, in order of severity:**

- AuthKit misbehaves with Claude's DCR → `GoogleProvider` fallback documented
  in `spike/NOTES.md` (needs a Google Cloud OAuth app).
- Connectors unusable on her tier/mobile → surface decision reopens (PWA);
  store and tools unaffected.
- rif dies someday → `python -m rif.export` renders every space back to
  portable markdown; plus the R2 dumps; plus Railway managed backups.

**Where things live:** code+plan on `diepzee/rif`, `main` (the `build-v1` and
`piccolo-port` branches are merged); knowledge design in
`mark/meta/architecture.md` (branch `forest-leech`); build audit trail in
`.worktrees/build/.superpowers/sdd/2026-08-05-rif-v1/progress.md`.

**Public URLs:** marketing page at `https://reefwith.me/`, the app at
`/app`, MCP at `/mcp`. The `rif-app-production.up.railway.app` hostname still
answers, but connectors must use `reefwith.me` — see "Custom domain".

**How it deploys:** merging to `main` auto-deploys production (Railway,
dashboard setting, no CI). `railway up` is the manual override. See
"Phase 3 → How deploys actually happen".
