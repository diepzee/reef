# Identity-table RLS — design

**Date:** 2026-08-11 · **Status:** draft, awaiting review
**Scope:** row-level security for `persons`, `spaces`, `memberships` — the
three tables `src/rif/rls.py` does not cover.

Two external plan reviews (GPT, via the Plan Reviewer expert) rejected
earlier revisions of this design. Every condition from both reviews is folded
in below; where a review left a choice open, the choice is made here and
marked **Decision**.

---

## 1. Goal, and the one it replaces

The stated goal was "a database backstop for email disclosure". Review 1
showed a row-level policy cannot deliver that: RLS filters rows, not columns,
so any policy that lets a co-member see your `persons` row hands them your
email with it.

The actual goals are therefore:

1. **Fail closed.** A forgotten application-level filter on the identity
   tables must return nothing, not everything. Today `rif_app` can run any
   query it likes against all three tables.
2. **Owner-only email disclosure, enforced by Postgres.** Achieved not by a
   policy but by *removing* general SELECT access to other people's person
   rows and routing rosters through definer functions with the authority
   check inside the function (§4).
3. **No standing enumeration.** Pre-auth identity binding today can run
   arbitrary `WHERE` clauses against `persons`. Afterwards it can fetch
   exactly one row by exact unique key, through a function.

**Non-goals:** protecting content from the operator (impossible — the server
reads plaintext by design; never claim otherwise), client-side encryption
(out of scope, product decision not made), and column-level privileges as a
general mechanism (used in exactly one narrow place, §5).

## 2. Why the obvious designs fail (review findings, kept for the record)

- **A `memberships` policy that subqueries `memberships` recurses.**
  Postgres expands the policy while evaluating the policy and raises
  `infinite recursion detected`, it does not converge.
- **`SECURITY DEFINER` owned by the migration role does not help.** Under
  `FORCE ROW LEVEL SECURITY` the *table owner is also subject to policies*
  (that is what FORCE means), so a definer function owned by the table owner
  recurses into the same policy it was meant to underpin.
- **Existing content policies sit on this substrate.** The deployed
  `pages`/`attachments`/`revisions` predicates (`rls.py:72-94`) subquery
  `memberships`; enabling RLS there changes their behavior. This work is
  surgery on the live authorization system, not an addition beside it.
- **A member-scoped UPDATE policy on `spaces` is row-level, so it would let
  any member rewrite `slug`, `kind`, and `owner_person_id` by direct SQL.**
  Yet `pages.py:125` bumps `spaces.version` on every content write by any
  member, so owner-only UPDATE breaks all shared-cove writes.

## 3. The authorization primitive: a `BYPASSRLS` function-owner role

A dedicated role owns every helper function:

```sql
CREATE ROLE rif_authz NOLOGIN BYPASSRLS;   -- requires superuser; §8
```

`NOLOGIN`: nothing can connect as it. `BYPASSRLS`: queries inside functions
it owns see tables without policy filtering — which is precisely what makes
the policies non-recursive. Every function below is:

- `SECURITY DEFINER`, owned by `rif_authz`
- `SET search_path = public, pg_catalog` (fixed, injection-proof)
- `REVOKE EXECUTE ... FROM PUBLIC; GRANT EXECUTE ... TO rif_app`
- `STABLE` if read-only, `VOLATILE` if it writes
- returning the narrowest useful shape — never `SETOF persons`

These functions are *the* trusted computing base of the system. The list is
closed: adding one is a security-review event, and each carries a docstring
comment stating its authority check.

### The two predicate functions

```sql
rif_space_ids()        RETURNS SETOF uuid  -- memberships of the armed principal
rif_member_space_ids() RETURNS SETOF uuid  -- same, role = 'member'
```

Both read `current_setting('app.person_id', true)` — transaction-local
`set_config` state is visible inside a definer function (it is session
state, not role state; confirmed in review 2). All policies, content and
identity alike, call these instead of subquerying `memberships` directly.

### The pre-auth functions (identity binding)

```sql
rif_person_by_subject(text)   -- STABLE; one row (id, email, display_name) by exact subject
rif_person_bind(text, text)   -- VOLATILE; atomic: UPDATE persons SET subject=$2
                              --   WHERE email=$1 AND subject IS NULL RETURNING id, email, display_name
rif_person_by_email(text)     -- STABLE; dev/CLI fallback paths and tests only
rif_person_alive(uuid)        -- STABLE; boolean — the web cookie's "does this person still exist" check
```

`rif_person_bind` folds the lookup and the subject-binding UPDATE into one
statement, eliminating the race review 1 flagged between `auth.py:37` and
`auth.py:44`. Two concurrent first-sign-ins: one binds, the other returns
zero rows and re-resolves by subject.

### The disclosure functions (rosters and names)

```sql
rif_roster(uuid)              -- (display_name) per member; check: caller ∈ space (rif_space_ids)
rif_roster_with_emails(uuid)  -- (display_name, email); check: caller = spaces.owner_person_id
rif_space_owner(uuid)         -- (display_name, email) of the owner; check: caller ∈ space
rif_display_names(uuid[])     -- (person_id, display_name); no membership check — see Decision
rif_my_invitees()             -- (email, display_name, created_at, joined bool); check: invited_by = caller
rif_invites_minted_since(timestamp) -- bigint; check: counts only invited_by = caller
```

**Decision — `rif_space_owner` keeps today's contract:** every member sees
the owner's email (`routes_api.py:355-368` does this deliberately — the
owner is the cove's accountability point). Review 2 asked for an explicit
ruling; this is it.

**Decision — `rif_display_names` takes any ids and returns display names
only.** Display names are shown on every shared surface already (rosters,
avatars, revision history); ids are unguessable uuid4s learned only through
membership. Scoping this function to "authors of revisions in my spaces"
would cost a join per call for no confidentiality gain. Emails and subjects
never pass through it. This serves `context.py:150` and `export.py:288`
(revision author names), which review 2 caught as silently breaking.

**Decision — `persons_invitee_select` from revision 2 is dropped.** It
exposed whole rows including the later-bound `subject`. The invite budget
(`invitations.py:85`) uses `rif_invites_minted_since`; the invites screen
uses `rif_my_invitees`. Both return only inviter-supplied fields plus a
join-status boolean.

### The mutation functions (privileged writes)

```sql
rif_person_id_by_email(text)  -- STABLE; uuid or NULL — invite path pre-check, id only
rif_remove_member(uuid, uuid) -- VOLATILE; (removed bool, person_erased bool)
rif_transfer_space_ownership(uuid, uuid) -- VOLATILE; account-deletion succession
```

**Decision — `remove_member`'s person-cleanup moves inside the function.**
Today `spaces.py:211-232` deletes an unbound invitee's person row after
their last membership goes — which needs a person-wide membership count the
remover has no right to see. `rif_remove_member(space_id, person_id)`
checks the caller owns the space, deletes the membership, and erases the
person iff `subject IS NULL AND NOT EXISTS (memberships)` — atomically, no
TOCTOU, and the "last membership" invariant is now enforced where it can
actually be evaluated. This answers review 2's gaps 3 and 4 together.

**Decision — ownership transfer becomes a function, not a policy.**
`account.py:59-81` (deletion succession) updates *another person's*
membership role and the space's `owner_person_id`. No sane row policy
allows that without allowing far more. `rif_transfer_space_ownership(space,
successor)` checks the caller is the current owner, promotes a viewer
successor if needed, and reassigns — one audited place instead of two
permissive policies.

## 4. The policies

All `ENABLE` + `FORCE ROW LEVEL SECURITY`, per-command, following the
existing `rls.py` conventions (`NULLIF(current_setting(...), '')::uuid`
idiom throughout; call it `principal` below).

**persons** — self only, plus inviter-accountable insert:

```sql
persons_self_select  FOR SELECT USING (id = principal)
persons_self_update  FOR UPDATE USING (id = principal) WITH CHECK (id = principal)
persons_self_delete  FOR DELETE USING (id = principal)
persons_invite_insert FOR INSERT WITH CHECK (invited_by_person_id = principal)
```

No co-member visibility at all: names flow through `rif_roster`/
`rif_display_names`, emails through the owner-checked functions. *This* is
the email backstop — the row is simply not readable.

**spaces:**

```sql
spaces_member_select FOR SELECT USING (id IN (SELECT rif_space_ids()))
spaces_owner_select  FOR SELECT USING (owner_person_id = principal)  -- bootstrap: space visible before first membership
spaces_owner_insert  FOR INSERT WITH CHECK (owner_person_id = principal)
spaces_member_update FOR UPDATE USING (id IN (SELECT rif_member_space_ids()))
                     WITH CHECK (id IN (SELECT rif_member_space_ids()))
spaces_owner_delete  FOR DELETE USING (owner_person_id = principal)
```

**The version-bump column narrowing (the one column-privilege use):**
`spaces_member_update` alone would let members rewrite `slug`/`kind`/
`owner_person_id`. So additionally:

```sql
REVOKE UPDATE ON spaces FROM rif_app;
GRANT UPDATE (version) ON spaces TO rif_app;
```

Members can update exactly one column (`version`) on exactly their member
spaces. Ownership transfer — the only other legitimate spaces UPDATE — goes
through `rif_transfer_space_ownership` (definer, bypasses the grant).
Row policy narrows *which rows*, column grant narrows *which columns*;
together they express what neither can alone. This answers review 2's gap 2.

**memberships:**

```sql
memberships_self_select  FOR SELECT USING (person_id = principal)
memberships_covis_select FOR SELECT USING (space_id IN (SELECT rif_space_ids()))
memberships_insert       FOR INSERT WITH CHECK (
                             space_id IN (SELECT rif_member_space_ids())
                             OR rif_owns_space(space_id))
memberships_self_delete  FOR DELETE USING (person_id = principal)  -- leave a cove
```

Non-recursive because `rif_space_ids()` bypasses. `rif_owns_space(uuid)`
(one more STABLE definer: `owner_person_id = principal`, bypassing the
spaces policies) covers the bootstrap insert — first membership at space
creation, before any membership exists. Owner-removes-member and role
promotion have **no policy at all**: they live exclusively in
`rif_remove_member` / `rif_transfer_space_ownership`. No
`memberships_update` policy exists — nothing legitimate updates a
membership row outside the transfer function.

FK actions on person delete (`memberships` CASCADE, `revisions.author_id` /
`invited_by` SET NULL — migration `rif_2026_08_11t20_10_00_000000`) are
referential-integrity internals and bypass RLS (confirmed review 2, ruling
iii). Account deletion's final step survives.

## 5. Application changes

- **Arm immediately after identity resolution.** `auth.py`: build the
  `Principal` from the function result and `arm()` *before* anything else —
  the subject binding is already inside `rif_person_bind`, and
  `ensure_personal_space` then runs armed (its Space insert passes
  `spaces_owner_insert`; its Membership insert passes the `rif_owns_space`
  arm).
- **Fix the pre-arming reads** review 1 caught: `account.py:44` and
  `export.py:89` read `Person` before `accessible_spaces()` arms — reorder
  or arm explicitly first. CLI export (`export.py:441`) arms right after
  its `rif_person_by_email` lookup. Web cookie check (`routes_api.py:185`)
  becomes `rif_person_alive`.
- **Rewrite the consumers:** `spaces.py` `member_names`/`member_roster` →
  `rif_roster`/`rif_roster_with_emails`; `routes_api.py:360` →
  `rif_space_owner`; `context.py:150` + `export.py:288` author names →
  `rif_display_names`; `invitations.py:85,132` → the invite functions;
  `spaces.py:211-232` → `rif_remove_member`; `account.py` succession →
  `rif_transfer_space_ownership`.
- **Slug check** (`spaces.py:127`) goes blind under RLS: catch
  `UniqueViolationError` from the insert and report `slug_taken`. Kills the
  current existence oracle as a side benefit.
- `rls.py` gains `identity_statements()` / `disable_identity_statements()`
  plus the function DDL, remaining the single source of truth for migration
  and conftest alike.

## 6. Deploy phasing — each phase is one PR, merged and watched alone

`main` auto-deploys; migrations run before the server starts within a
deploy, so migration-before-code holds *within* a phase. The phases exist
so no deploy both changes the substrate and depends on it.

1. **PR-1 — primitive + rewrite existing content predicates.** Create
   `rif_authz`, the predicate functions, grants; swap `pages`/
   `attachments`/`revisions`/`promotions` predicates onto
   `rif_space_ids()`/`rif_member_space_ids()`. Semantics identical (same
   subquery, now inside a bypassing function). If anything is wrong with the
   primitive, it fails inside the already-covered tables under full test
   coverage — not during a login outage.
2. **PR-2 — application hardening, no new policies.** All definer lookup/
   mutation functions land in a migration; app code moves onto them and the
   arming reorders happen. Behavior identical; identity tables still
   uncovered. Soak.
3. **PR-3 — the identity policies + column grant.** By now no application
   code reads another person's row directly. One migration:
   ENABLE/FORCE + policies + the `spaces` column grant. The negative tests
   (§7) merge in the same PR.

Rollback for each phase is a revert PR; `disable_identity_statements()`
mirrors idempotently (`DROP POLICY IF EXISTS` / `DROP FUNCTION IF EXISTS`),
matching the existing convention.

## 7. Tests

Review 2's condition: same-statements-in-conftest is no longer enough,
because definer behavior depends on *who owns what*. `tests/conftest.py`
therefore creates the production role shape in the test cluster:
`rif_authz`-equivalent (NOLOGIN BYPASSRLS) owning the functions, and a
non-owner `rif_app`-equivalent that the assertion connections use.

New tests, all direct SQL as the app role against real Postgres:

- **Recursion smoke:** SELECT on every RLS table, armed and unarmed — no
  `infinite recursion` errors anywhere.
- **Fail-closed:** unarmed → zero rows on all six tables; armed as A → B's
  person row, B's foreign memberships, foreign spaces all invisible.
- **Disclosure:** member gets names, not emails; owner gets emails;
  non-member calling a roster function gets nothing; owner email visible to
  plain members via `rif_space_owner` (the kept contract).
- **Column grant:** member can bump `version`; member's direct UPDATE of
  `slug`/`owner_person_id` fails with insufficient privilege.
- **Binding race:** two concurrent `rif_person_bind` calls — one wins.
- **Flows end-to-end under the policies:** first sign-in → onboarded;
  invite existing + new person; remove member incl. unbound-invitee
  erasure; leave cove; account deletion incl. succession; full/single/CLI
  export; invite budget counting.
- The existing 215 keep passing — content-table semantics must not move.

## 8. Operational notes

- `CREATE ROLE ... BYPASSRLS` requires superuser. Provisioning extends
  `scripts/provision_app_role.py` (which already runs as Railway's
  superuser credential once, by hand) — it is *not* part of the boot
  migration, which runs as the non-super admin role. The provisioning step
  also runs `GRANT rif_authz TO <admin role>`, because migrations create
  the functions and `ALTER FUNCTION ... OWNER TO rif_authz` requires the
  executing role to be a member of the new owner. Runbook gains the step;
  the migration that installs functions fails loudly if the role is
  missing.
- The Logfire access trail (Task C) should record calls to the mutation
  functions once observability lands — they are the privileged surface.

## 9. Explicitly accepted risks

- `rif_display_names` returns display names for arbitrary known ids
  (uuid4-unguessable). Accepted: display names are the product's public
  face internally; emails/subjects are not reachable this way.
- The function list is a trusted computing base owned by a BYPASSRLS role.
  Accepted deliberately over "policies only": the reviews established that
  policies alone cannot express invitation, succession, or cleanup without
  being permissive enough to be worthless. Small, closed, reviewed set.
- Members of a cove can see co-membership *rows* (ids, roles) via
  `memberships_covis_select`. Names/emails stay behind the functions.
