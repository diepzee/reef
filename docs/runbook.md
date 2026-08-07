# rif — go-live runbook

rif went live on **6 Aug 2026**. This document is now two things: a record of
how it got there, and the list of what is still open. Each phase explains what
it does, why it exists, and what "done" looks like. The completed phases keep
their instructions rather than being deleted — they are what you would follow
to stand the service up again.

| Phase | State |
|---|---|
| 1 — Prove the connection works | **Done**, 6 Aug 2026 |
| 2 — Identity seeds | **Partly done** — one person seeded; the second member is not |
| 3 — Deploy the real service | **Done**, 6 Aug 2026 |
| 4 — Storage and safety nets | **Open — the priority.** Not started: no R2, no backup cron. Real data is in the store with only Railway's managed snapshots behind it, and both image tools fail |
| 5 — Content: the import | **Done**, 6 Aug 2026 |
| 6 — Protocol and personas | **Partly done** — `meta/protocol.md` written; `meta/persona.md` is still the smoke-test placeholder |
| 7 — Measure the context ceiling | **Open** |

**Start with Phase 4.** The ordering below is the original build order, which
put the connector gate first because everything depended on it. That gate has
passed. The risk has moved: there is now a real corpus in production — a
health page compiled from a full dossier, a character portrait, work and
finance pages — none of it in git, and none of it yet proven to survive a
restore.

---

## Phase 1 — Prove the connection works

> **Done, 6 Aug 2026 — for his account, on desktop.** The connector is
> configured against the real service at
> `https://rif-app-production.up.railway.app/mcp` and answers tool calls from
> Claude Code. Claude registered itself with no client id or secret entered
> anywhere, so Dynamic Client Registration works. Identity binding works too:
> `principal_from_claims` requires a verified email to bind an unknown
> subject, and it succeeded, so AuthKit's **access token does carry `email`
> and `email_verified`** — the open question flagged in `spike/NOTES.md`.
>
> **Desktop is confirmed (7 Aug 2026). Steps 5 and 6 are not:** the connector
> has not been tried on a phone, and her account and tier have not been tried
> at all. Step 6 is blocked behind Phase 2 regardless — an authenticated
> stranger is denied until she is on the allowlist.
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

## Phase 2 — Identity seeds

**Why this table matters:** `persons` is the allowlist. WorkOS will
authenticate anyone who signs up; this table is what turns "authenticated"
into "allowed". An authenticated stranger is denied by
`principal_from_claims`, and RLS denies them rows even if that check were
bypassed.

**Already done.** The seed migration creates one person -- Wouter -- his
personal space, and the household space. You can deploy and use the system
solo today.

### Adding the second member

Her row is deliberately absent rather than a placeholder. Her email is the
key her first login binds against, and a placeholder would put an unusable
address in production that no later run of the seed would correct, because
migrations do not re-run.

When her address is settled, add a new migration alongside the others:

```sql
INSERT INTO persons (id, email, display_name)
VALUES ('<new-uuid>', '<her-email>', '<her-name>');

INSERT INTO spaces (id, slug, kind, owner_person_id, version)
VALUES ('<new-uuid>', 'partner', 'personal', '<her-person-id>', 0);

INSERT INTO memberships (person_id, space_id) VALUES
  ('<her-person-id>', '<her-space-id>'),
  ('<her-person-id>', '55555555-5555-5555-5555-555555555555');
```

That last id is the household space, seeded already.

**Get the address exactly right**, including which Gmail if she has more
than one. `rif` binds to the provider's subject on first login, so changing
her account afterwards means clearing her `subject` column by hand.

---

## Phase 3 — Deploy the real service

> **Done, 6 Aug 2026.** The real service runs at
> `rif-app-production.up.railway.app`, and step 3's check passes: `list_spaces`
> returns exactly `personal` and `household` as aliases, with no space names
> and nothing belonging to anyone else.

**Why it's safe now:** the server *refuses to boot* HTTP without auth
configured (a final-review fix — misconfiguration is a crash, not an open
endpoint), and the store is empty anyway until Phase 5.

1. ```bash
   railway add --database postgres
   railway up
   ```
   The Dockerfile runs `scripts/migrate.py` on boot (advisory-locked), which
   applies the schema, RLS policies, and your Phase 2 seed.
2. **Swap the connector** on both accounts from the spike URL to the real
   `/mcp` URL (same domain if you reused the service — then nothing to swap).
3. **Verify, on each phone:** `list_spaces` returns exactly that person's
   `personal` + `household` — never the other person's space, never the word
   `school` (aliases only; that's a tested invariant, but see it once with
   your own eyes).
4. **Verify the lock:** `curl -i https://<domain>/mcp` from anywhere →
   rejected, not a tool listing.
5. Commit ticking Task 6's deploy checkboxes — the code commit deliberately
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

> **Done, 6 Aug 2026.** The store holds 16 personal pages and 5 household
> pages. The final disposition differs from the sketch in step 2 below: the
> household layer came out as `house.md`, `future-home.md` and `travel.md`
> rather than the `money.md` / `family-film.md` split proposed here, and
> `health.md` and `finances.md` both stayed personal.
>
> **Step 4's cross-check has not happened** — verifying from her phone that
> household pages are visible and personal ones are not needs Phase 2 first.
> That check is the moment the privacy design faces reality, and it is still
> outstanding.

**Why the ceremony:** this is the one step that moves your actual life into
the system, and the disposition (what's household vs private) is a set of
judgment calls only you can make. The importer takes explicit filename lists —
nothing moves by inference.

1. **Finalize the disposition** in `mark/meta/architecture.md` (branch
   `forest-leech`). Two calls are still open, flagged since the first draft:
   - `health.md` — private by default, but this is the page where the default
     deserves an actual decision (a shared summary is possible without moving
     the investigation notes).
   - `finances.md` — the proposed split sends joint account/2034 plan/family
     loan to household and keeps pension+insurance private (it references the
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
   everything; her phone — household pages visible, none of yours. This is the
   moment the privacy design faces reality; look at it directly.

---

## Phase 6 — Protocol and personas

> **Step 1 done, 6 Aug 2026** — `meta/protocol.md` is written in the household
> space. **Step 2 is not:** `meta/persona.md` is still the 157-byte
> placeholder from the first end-to-end test, while every other page got real
> content. It is the page that steers the assistant's voice, and `mark.md` in
> the personal space already holds much of what belongs in it. Step 3 waits
> on Phase 2.

The code ships a built-in fallback protocol, but the real thing is content:

1. Write `meta/protocol.md` in the household space (via `update_meta_page` or
   ask any connected assistant): the operating rules — load context first,
   compile don't dump, private-by-default routing, promote-only-with-consent —
   plus the **onboarding behavior**: an assistant meeting an empty personal
   space introduces itself, asks what to call itself, and interviews gently to
   seed the persona.
2. Write your own `meta/persona.md` (Mark's tone lives in
   `mark/wiki/mark.md` today — port it).
3. **Hers is not yours to write.** Her first conversation, via the onboarding
   behavior, creates it. She names her own assistant.

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

## Reference

**Env vars (Railway):**

| Var | Purpose |
|---|---|
| `WORKOS_AUTHKIT_DOMAIN` | AuthKit app domain; auth refuses to boot without it |
| `RIF_BASE_URL` | Public root URL, no path; drives advertised resource URL + token audience |
| `DATABASE_URL` | The **constrained** `rif_app` role — no DDL, subject to RLS. Do not point this back at Railway's injected `${{Postgres.DATABASE_URL}}`: that is the superuser, and it turns every policy off |
| `RIF_MIGRATION_DATABASE_URL` | The admin role. Used by `scripts/migrate.py` for DDL on boot, and by the backup cron for `pg_dump`. Never read by the server |
| `RIF_S3_ENDPOINT` / `RIF_S3_BUCKET` / `RIF_S3_ACCESS_KEY` / `RIF_S3_SECRET_KEY` | R2 for images + backups |
| `RIF_CONTEXT_CHAR_BUDGET` | Set from Phase 7 measurement |
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
