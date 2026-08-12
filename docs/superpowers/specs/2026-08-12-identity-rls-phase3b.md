# Phase 3b — the identity policies themselves

**Status:** ready to implement, not started. Phases 1, 2 and 3a are merged and
live in production.

This is the last phase and the only one that changes behaviour. Everything
before it was groundwork, deployable and reversible on its own. This one can
lock every user out of every tool call, so it gets its own session, its own
review pass, and the extra test harness described in §5 before it merges.

Read alongside `2026-08-11-identity-rls-design.md`, which holds the reasoning
this addendum assumes.

---

## 1. What is already true (do not redo)

- `rif_authz` exists in production: `NOLOGIN BYPASSRLS`, owns every helper
  function and nothing else. The phase-1 migration creates it automatically
  when the migration credential is a superuser (Railway's is), and refuses
  with operator instructions when it is not.
- Content policies (`pages`, `attachments`, `revisions`, `promotions`) already
  resolve through `rif_space_ids()` / `rif_member_space_ids()`. They will not
  recurse when `memberships` gains a policy. **That is the whole reason
  phase 1 existed.**
- Identity binding runs through `rif_person_by_subject` /
  `rif_person_bind` / `rif_person_by_email` / `rif_person_alive`. Subject
  binding is atomic.
- Disclosure runs through `rif_roster` / `rif_space_owner` /
  `rif_display_names` / `rif_person_id_by_email` / `rif_invites_minted` /
  `rif_oldest_invite`.
- Every path that needs a principal now arms one: `principal_from_claims`
  (before onboarding), the web `api()` wrapper, `create_space`,
  `invites_left`, `next_invite_at`, `allowlist`, and — via
  `accessible_spaces` — `account.delete_account_rows` and `export._export_rows`.

## 2. What phase 3b must add

### Two mutation functions

`_function_ddl` needs a `language` parameter; both of these are `plpgsql`.

**`rif_remove_member(p_space uuid, p_person uuid, OUT removed boolean, OUT person_erased boolean)`**
— `VOLATILE`, writes `memberships` and `persons`. Body: return early unless
the caller owns `p_space`; return early if `p_person` is the caller; delete
the membership and report `removed` from `ROW_COUNT`; then erase the person
iff `subject IS NULL AND NOT EXISTS (SELECT 1 FROM memberships WHERE
person_id = p_person)`.

This exists because `spaces.remove_member` currently counts a *person-wide*
membership total (`spaces.py`, the `Membership.count()` call) to decide
whether an unbound invitee is now orphaned. The remover has no right to see
memberships in spaces they are not in, so under the policies that count comes
back short and the invitee is erased while still a member elsewhere. Moving
the whole decision inside one function makes it atomic and evaluable.

**`rif_transfer_space_ownership(p_space uuid, p_successor uuid) RETURNS boolean`**
— `VOLATILE`. Return false unless the caller currently owns `p_space` and
`p_successor` has a membership in it; promote a viewer successor to `member`;
reassign `owner_person_id`; return true.

`account.delete_account_rows` updates *another person's* membership row and
the space's owner. No row policy can permit that without permitting far more.

**`rif_owns_space(p_space uuid) RETURNS boolean`** — `STABLE`, needed by the
`memberships` insert policy below.

### The policies

All `ENABLE` + `FORCE ROW LEVEL SECURITY`, per command, using the existing
`PRINCIPAL` idiom.

```
persons_self_select    SELECT USING (id = PRINCIPAL)
persons_self_update    UPDATE USING (id = PRINCIPAL) WITH CHECK (id = PRINCIPAL)
persons_self_delete    DELETE USING (id = PRINCIPAL)
persons_invite_insert  INSERT WITH CHECK (invited_by_person_id = PRINCIPAL)

spaces_member_select   SELECT USING (id IN (SELECT rif_space_ids()))
spaces_owner_select    SELECT USING (owner_person_id = PRINCIPAL)
spaces_owner_insert    INSERT WITH CHECK (owner_person_id = PRINCIPAL)
spaces_member_update   UPDATE USING (id IN (SELECT rif_member_space_ids()))
                              WITH CHECK (id IN (SELECT rif_member_space_ids()))
spaces_owner_delete    DELETE USING (owner_person_id = PRINCIPAL)

memberships_self_select   SELECT USING (person_id = PRINCIPAL)
memberships_covis_select  SELECT USING (space_id IN (SELECT rif_space_ids()))
memberships_insert        INSERT WITH CHECK (
                            space_id IN (SELECT rif_member_space_ids())
                            OR rif_owns_space(space_id))
memberships_self_delete   DELETE USING (person_id = PRINCIPAL)
```

`spaces_owner_select` is not redundant with `spaces_member_select`: at space
creation the row exists before its first membership, so without it the
`memberships_insert` check cannot see the space it is about to join and the
first membership fails — locking a new user out of onboarding.

There is deliberately **no** `memberships_update` policy and no
owner-removes-member `DELETE` policy. Those operations exist only inside the
two definer functions above, which is what makes them auditable.

### The column grant

`spaces_member_update` alone would let any member rewrite `slug`, `kind`, and
`owner_person_id` by direct SQL, because RLS constrains rows, not columns. So
additionally, for the app role:

```sql
REVOKE UPDATE ON spaces FROM <app role>;
GRANT UPDATE (version) ON spaces TO <app role>;
```

`pages.py` bumps `spaces.version` on every page write by any member, which is
why the policy cannot be owner-only. Ownership transfer is the only other
legitimate `spaces` update and goes through the definer function, which
bypasses the grant.

Use the same guarded `DO $do$ ... IF EXISTS (SELECT 1 FROM pg_roles ...)`
pattern `_function_ddl` uses for grants, since the role is `rif_app` in
production and `rif` in dev/test.

## 3. Application changes

- `spaces.remove_member` → call `rif_remove_member`, keeping the existing
  `SpaceError` messages for the not-a-member and cannot-remove-yourself cases
  (distinguish them *before* calling, using `rif_person_id_by_email` and the
  existing `_membership` lookup, which stays readable via
  `memberships_covis_select`).
- `account.delete_account_rows` → replace the successor-selection block with
  `rif_transfer_space_ownership`. The successor choice (prefer a full member,
  then lowest id) moves into the function so it stays deterministic.
- `spaces.create_space` → the global slug check goes blind under
  `spaces_member_select`. Catch `asyncpg.exceptions.UniqueViolationError` from
  the insert and raise the existing "already exists" `SpaceError`. This also
  removes a small existence oracle.

## 4. Migration

One migration: policies + column grant, calling a new
`identity_policy_statements()` in `rif.rls` so `conftest` applies exactly the
same DDL. Mirror it in `disable_statements()`. The functions from §2 should
land in the **same** migration, before the policies that call them.

## 5. The test-harness gap — do this first

`tests/conftest.py` connects as `rif`, which **owns** the tables. Production's
`rif_app` does not. Two consequences the current suite cannot see:

1. **The column grant cannot be verified.** A table owner's privileges are
   implicit; revoking `UPDATE` and granting `UPDATE (version)` does not bite
   the same way. A test asserting "a member cannot rewrite `slug`" would pass
   locally for the wrong reason.
2. Adversarial direct-SQL negatives ("armed as A, select B's person row")
   should run as a non-owner to match production.

So add a `rif_probe` role — `LOGIN`, no ownership, granted the same DML as
`rif_app` — created in `docker/initdb/01-create-app-role-and-databases.sql`
and via a one-liner in the conftest error message, exactly as `rif_authz` was
handled. Point the new negative tests at a connection opened as that role.

This was condition 9 of the second external review. Without it the suite
proves a shape production does not have — which this repo has been burned by
before (see the `docker/initdb` header comment about production running as
superuser for five days while tests looked honest).

## 6. Tests to write

- Recursion smoke: select from every RLS table, armed and unarmed, no
  `stack depth limit exceeded`.
- Fail closed: unarmed → zero rows on all six tables.
- Armed as A: B's person row invisible; B's memberships in foreign spaces
  invisible; foreign spaces invisible.
- Column grant (as `rif_probe`): `UPDATE spaces SET version = version + 1`
  succeeds for a member; `UPDATE spaces SET slug = ...` and
  `SET owner_person_id = ...` are refused.
- Full flows under the policies: first sign-in end to end; create cove;
  invite new and existing person; remove member including unbound-invitee
  erasure; leave cove; account deletion including succession; full, single
  and CLI export; invite budget counting.
- The 242 existing tests must still pass unchanged.

## 7. Deploy

`main` auto-deploys. Merge alone, nothing else in flight, and watch. Note that
Railway's rolling deploy has a brief window where neither container serves —
a 502 was observed at the cutover instant on 12 Aug 2026 — so a single failed
probe during the swap is expected and is not evidence of a broken policy set.

**Rollback:** `disable_statements()` drops the identity policies and functions
idempotently. A revert PR is the fast path; the policies can also be dropped
by hand from the Postgres service if the app is wedged.

**The failure mode to watch for** is not an error page. It is a user who can
sign in but sees an empty cove list, which means a policy is filtering
something the application still believes it can read.

---

## 8. Findings from the first implementation attempt (12 Aug 2026)

Written during a partial implementation on branch `rls-identity-policies`.
The policy DDL, the three mutation functions, and the seeding harness are on
that branch and work; the app rewrites and the remaining test surgery are not
done. Start here rather than from §2.

### `INSERT ... RETURNING` is governed by the SELECT policy

The blocker, and worth knowing before writing a line: Postgres applies
**SELECT** policies to the rows returned by an `INSERT ... RETURNING`, and
Piccolo's `save()` always emits `RETURNING`. With `persons` self-only, an
inviter creating an invitee is refused — even though `persons_invite_insert`
is satisfied exactly.

The error names the wrong thing:

    new row violates row-level security policy for table "persons"

which reads as a `WITH CHECK` failure and is not. Reproduced in isolation
with the principal armed and `invited_by_person_id` equal to it.

**Do not fix this by adding `persons_invitee_select USING
(invited_by_person_id = PRINCIPAL)`.** That was rejected in §2 for a reason
that still holds: it exposes the invitee's whole row, including the
`subject` bound later, when the entitlement is only to fields the inviter
supplied.

The consistent fix is a definer function — `rif_allowlist_person(email,
display_name)` returning the new id — so the invite insert never needs
`RETURNING` under the caller's policies. `rif.invitations.allowlist` calls
it in place of `Person(...).save()`.

### Fixture data must be seeded out of band

`persons_invite_insert` demands an inviter, so a person with no inviter
cannot be created by the application at all — which is correct, and is why
reef's own first person was made by hand. Test fixtures represent rows that
already exist, so `conftest.Graph` now writes through a superuser connection
(`seed_dsn()`, overridable with `RIF_SEED_DATABASE_URL`).

Two traps that cost time:

- `memberships.id` is a serial, not a uuid. Let it default.
- A row written behind Piccolo's back still has `_exists_in_db = False`, so a
  later `.save()` INSERTs a duplicate instead of updating. The builders set
  the flag; anything else seeding directly must too.

### Where the suite stood

251 → 216 passing, 35 failing, when the policies were switched on. The
failures were not noise; they decompose into:

1. the `RETURNING` problem above (most of them — every invite path),
2. tests that mutate a seeded row via `.save()`, which is now an unarmed
   `UPDATE` and is filtered to zero rows rather than erroring — silent, and
   the reason `test_identity` fails with a bare `assert True is False`,
3. `remove_member` and account-deletion succession, which still need their
   definer functions wired in per §3.

Class 2 deserves emphasis: **a filtered `UPDATE` or `DELETE` affects no rows
and raises nothing.** Any code that assumed a write happened will carry on
with stale state. That is the failure mode to hunt for in review, not
exceptions.
