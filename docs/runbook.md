# rif — go-live runbook

Everything the agents could build is built and reviewed: 51 tests green on
`build-v1`. What remains is the part that needs *you* — accounts, dashboards,
deploys, and two phones. This document explains each step: what it does, why
it exists, and what "done" looks like.

Rough shape: **three evenings.** Phase 1 is the gate; nothing else is worth
starting until it passes. Phases 2–4 are one sitting at a terminal. Phases 5–7
are content and verification.

---

## Phase 1 — The gating check (WorkOS + spike + both phones)

**Why this is first:** the entire surface decision — her assistant living in
the Claude mobile app — rests on two unproven assumptions: that a custom MCP
connector works from the mobile app on **her plan tier**, and that Claude's
connector OAuth (which demands Dynamic Client Registration) actually completes
against WorkOS AuthKit. The spike exists to burn both unknowns down with zero
real data at stake. If it fails, nothing below it is wasted — the store, tools
and tests all survive — but the surface decision reopens (PWA route).

**Steps:**

1. **WorkOS account** (free tier; you need 2 users of their 1M): create an
   application with AuthKit enabled. Note the `https://<something>.authkit.app`
   domain.
2. In the WorkOS dashboard, enable **Dynamic Client Registration**. While
   you're there, check the Redirects tab — the spike flagged (unverified)
   that a callback entry may or may not be needed; record what you find.
3. **Deploy the spike:**

   ```bash
   cd ~/Repositories/haai/rif/.worktrees/build
   railway init --name rif && railway up && railway domain
   railway variables --set WORKOS_AUTHKIT_DOMAIN=<authkit-domain> \
                     --set RIF_BASE_URL=https://<railway-domain>
   railway up   # again, so the vars take
   ```

4. **claude.ai web, your account:** Settings → Connectors → add
   `https://<railway-domain>/mcp`. You should get bounced to an AuthKit login
   page; sign in; call the `whoami` tool. **Done when:** your email appears in
   the returned claims.
5. **Your phone:** confirm the connector shows up in the Claude mobile app and
   `whoami` still works.
6. **Her account, her phone:** add the same connector, sign her up through the
   AuthKit page, `whoami`. **This is the actual gate** — her tier, her device.
7. Record in `spike/NOTES.md`: the claims shape you saw (`sub`, `email`,
   `email_verified` — Phase 3's login code depends on these), DCR behavior,
   the redirects answer, any tier limits. Commit.

**If step 6 fails** (no connectors on her tier / not on mobile): stop, tell
me, and we reopen the PWA branch of the design. Everything in `src/rif/`
survives that pivot untouched.

---

## Phase 2 — Identity seeds

**Why:** the `persons` table is the allowlist — the only two people who can
ever get in. Her row was deliberately left as a placeholder because her email
is her identity to give, not something an agent guesses.

1. Edit `migrations/versions/d8567d9ee6dc_seed_persons_and_spaces.py`: replace
   `<HER-EMAIL>` and `<HER-NAME>` with the email she'll sign into AuthKit with
   (exact match matters — it's how her first login binds to her row) and her
   chosen display name.
2. Commit. Local check if you like: `uv run alembic upgrade head` against the
   docker Postgres.

**Note:** WorkOS authenticates anyone who signs up; this table is what turns
"authenticated" into "allowed". An authenticated stranger is denied by
`principal_from_claims`.

---

## Phase 3 — Deploy the real service

**Why it's safe now:** the server *refuses to boot* HTTP without auth
configured (a final-review fix — misconfiguration is a crash, not an open
endpoint), and the store is empty anyway until Phase 5.

1. ```bash
   railway add --database postgres
   railway up
   ```
   The Dockerfile runs `alembic upgrade head` on boot (advisory-locked), which
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

**R2 (images):**

1. Cloudflare dashboard → R2 → create bucket `rif`, **enable object
   versioning** (that's the undo button for attachment bytes — they're not in
   pg_dump).
2. Create an S3-compatible API token, then:

   ```bash
   railway variables --set RIF_S3_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com \
     --set RIF_S3_BUCKET=rif --set RIF_S3_ACCESS_KEY=<key> --set RIF_S3_SECRET_KEY=<secret>
   railway up
   ```

**Backups — read this part, it has a trap:**

The RLS design is doing its job *too* well: `FORCE ROW LEVEL SECURITY` means
even the table owner sees zero content rows without a principal set — and
`pg_dump` connects with no principal. **A backup run as the app role fails;
this was reproduced locally, not theorized.** The backup connection needs a
role with `BYPASSRLS`.

1. On Railway's Postgres, create/confirm a `BYPASSRLS` role (the default
   superuser-ish `postgres` role qualifies) and give `scripts/backup.py` *that*
   connection string — distinct from the app's `DATABASE_URL`.
2. Enable Railway's **managed Postgres backups** in the dashboard (belt).
3. Schedule `scripts/backup.py` as a daily Railway cron service (braces) — it
   streams `pg_dump` straight to R2, never to container disk, which is
   ephemeral.
4. **The drill — non-negotiable:** run one real backup, download the dump from
   R2, restore into local scratch per `docs/restore.md`, and check the counts:

   ```sql
   select (select count(*) from pages) pages,
          (select count(*) from revisions) revisions,
          (select count(*) from memberships) memberships;
   ```

   If `memberships` is 0, the backup is decorative. An untested backup is not
   a backup.

---

## Phase 5 — Content: the import

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
| `DATABASE_URL` | Injected by Railway Postgres (app role) |
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

**Where things live:** code+plan on `diepzee/rif` branch `build-v1`; knowledge
design in `mark/meta/architecture.md` (branch `forest-leech`); build audit
trail in `.worktrees/build/.superpowers/sdd/2026-08-05-rif-v1/progress.md`.
