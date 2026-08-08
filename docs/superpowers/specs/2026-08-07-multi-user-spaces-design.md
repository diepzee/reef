# Multi-user spaces — design

Written 7 Aug 2026. Extends `docs/spec.md` (rev 3): spaces stop being the two
privacy tiers of one household and become abstract, named groups of people.
Where this document contradicts `docs/spec.md`, this document wins; the main
spec gets a revision pass during implementation.

## Purpose

Let a space be any connected group of people — a school circle, an
accountant, a trip — not just "the household". Let a person belong to any
number of spaces. Let new people join by invitation, without an operator
hand-editing migrations.

## What stays true

- **RLS is the enforced boundary.** The content-table policies already grant
  access purely by membership-row existence; they are N-member-correct today
  and need no structural change.
- **Membership is the entire access model.** A member reads and writes
  everything in the space. Limited trust is expressed by making a smaller
  space and sharing extracts into it — not by per-page permissions.
- **Private by default.** Every person has exactly one personal space;
  `remember` defaults to it; sharing out of it is explicit, two-step, and
  permanent.
- **Extract, don't fragment.** Sharing copies content to the destination
  space and stubs the source. No transclusion, no cross-space reads.

## Conceptual model

A **space is a named group of people**. Two kinds:

- **Personal** — exactly one per person, auto-created at first sign-in,
  only ever one member, addressed by the reserved alias `personal` which
  resolves per-principal. Cannot be renamed, joined, or invited into.
- **Shared** — created by anyone, addressed by a globally-unique slug
  (`school`, `haai-admin`), owned by its creator, member set managed by the
  owner alone.

"Household" disappears as a concept. The existing household space becomes an
ordinary shared space (slug `school`, owner Wouter, same two members).

## Decisions, with the road not taken

| Decision | Chosen | Rejected because |
|---|---|---|
| Administration | Open invites: members bring new people in at runtime | Operator-only management doesn't scale past the household; the closed allowlist becomes invite-created rather than migration-created |
| Invite form | Email-bound: the invite *is* a person row + membership row; first verified-email sign-in binds it | Invite links need a redemption surface rif doesn't have, and a forwarded link admits anyone |
| Membership grants | Flat: member = full reader and writer | Per-role read/write matrices reopen the complexity the main spec rejected; smaller spaces do the same work. (But see "prepared, not built" below.) |
| Space authority | Creator-admin: the creator owns the space; only the owner invites and removes | Any-member invites make membership transitively viral — unknowable audiences under permanent sharing |
| Naming | Global slugs, one name per space for everyone | Per-person petnames make spaces unambiguous to no one but their namer; the "never leak another's space name" property protected privacy *tiers*, not groups people join on purpose |
| Personal space | Stays special (reserved alias, auto-created, uninvitable) | Fully-uniform spaces lose the guaranteed-private `remember` default and the persona/protocol anchor |
| Protocol location | Per-person, in the personal space | Merged per-space protocols let any co-member inject standing instructions into your assistant — a prompt-injection surface, unacceptable with open invites |

**Prepared, not built — read-only roles.** `memberships` gains a `role`
column (Postgres enum `member_role`: `member`, `viewer`; default `member`).
v1 creates only `member` rows and `invite` takes no role parameter. But the
RLS write predicate becomes role-aware *now*: reads (`USING`) require any
membership row; writes (`WITH CHECK`) require a membership row with
`role = 'member'`. Behavior is identical today, and an adversarial test
proves a hand-inserted `viewer` row can read but not write. Enabling viewers
later is purely additive — a role parameter on `invite`, a disclosure tweak,
docs. No RLS migration under pressure.

## Schema changes

- `SpaceKind`: rename `HOUSEHOLD` → `SHARED`
  (`ALTER TYPE ... RENAME VALUE`).
- `spaces.owner_person_id`: becomes NOT NULL. Drop the global UNIQUE
  constraint (a person may own many spaces). Add a partial unique index on
  `owner_person_id WHERE kind = 'PERSONAL'` so "one personal space per
  person" stays a database invariant.
- `memberships.role`: enum `member_role` (`member` | `viewer`), NOT NULL,
  default `member`. Existing rows backfilled `member`.
- `promotions.dest_space_id`: FK to `spaces.id`, NOT NULL after backfill
  (existing rows point at the school space).
- `persons.invited_by_person_id` (nullable FK) and `persons.created_at`:
  audit trail for who brought whom in. Seed-era rows keep NULL inviter.

Unchanged: `pages`, `attachments`, `revisions` (already space-scoped;
`Revision.author_id` already gives per-member attribution), optimistic
locking, version counters, opaque attachment keys.

## Addressing

`resolve_space(alias)`:

- `"personal"` → the principal's personal space (per-principal, as today).
- Any other string → shared-space lookup by slug, joined against the
  principal's membership. No membership → `AccessDenied`, same message shape
  whether the space is missing or merely not yours (no existence oracle).

The `len(spaces) != 1` invariant and both alias↔kind dictionaries
(`access.py:9`, `context.py:9`) are deleted. Index and context payloads name
spaces by `personal` / slug; cache version strings key by slug.

`list_spaces` returns, per space the principal belongs to:
`{name, members: [display names], you_are_owner, version}`. Members are
listed because with open invites, *knowing who is in the room* is the
informed-consent property — it must be one call away, not archaeology.

## Tool surface

New tools, all owner-checked in application code:

- `create_space(slug)` — creates a shared space; creator becomes owner and
  first member. Slug collision → clear error inviting another name. Slug
  rules: lowercase, hyphens, reserved words (`personal`) refused.
- `invite(space, email, display_name)` — owner-only. Creates the person row
  if the email is unknown (lowercased; `subject` NULL until first sign-in;
  `invited_by` recorded), then the membership row. Idempotent for an
  already-member email. Returns a disclosure: "«email» will permanently see
  everything in «space», past and future — «N» pages today."
- `remove_member(space, email)` — owner-only; owner cannot remove
  themselves. Deletes the membership row. Documented honestly: removal
  stops future access; it cannot unshare what was already read. Also the
  typo-repair path: removing a never-signed-in invitee (subject still NULL,
  no other memberships) also deletes the orphaned person row.

Changed tools:

- `prepare_to_share(path, dest_space, section?, dest_path?)` — source is
  always the personal space; `dest_space` is any shared space the principal
  belongs to. The disclosure enumerates the destination's members by display
  name: you confirm *who*, not just *where*. `confirm_share` writes to the
  staged `dest_space_id`.
- `remember(fact, space="personal")` — unchanged signature; `space` now
  accepts any slug the principal belongs to.
- Every `space: str` parameter elsewhere (read/write/edit/images) now takes
  `personal` or a slug. Signatures unchanged.

## Onboarding

First successful sign-in with an invited email follows the existing
verified-email binding in `auth.py`, unchanged, and then — in the same
transaction — creates the person's personal space, its membership row, and
two starter pages: `meta/persona.md` (stub for the person to fill in) and
`meta/protocol.md` (the operating-protocol template). `get_operating_protocol`
reads both from `personal` only.

The `auth.py` docstring's contract is rewritten: entry no longer requires a
migration; it requires an invitation from an existing member, bound by
verified email. What must still never exist is uninvited signup — a token
with an email no one invited is denied exactly as before.

## Security

- Content-table RLS: predicate split as described (role-aware `WITH CHECK`),
  otherwise untouched.
- `spaces` and `memberships` still carry no RLS; the three new mutating
  tools enforce owner-only rules in application code, with adversarial tests
  (non-owner invite/remove denied; non-member cannot address a space; a
  removed member loses reads on the next call).
- Invite scope: an invite admits a person to **one space**. A new person's
  blast radius is that space plus their own empty personal space.
- The paranoid-security-auditor reviews the invite/bind/onboard path before
  merge.

## Migration (one revision, applied in order)

1. Rename enum value `HOUSEHOLD` → `SHARED`; create `member_role`.
2. Add `memberships.role` (backfill `member`), `promotions.dest_space_id`
   (backfill school), `persons.invited_by_person_id` + `created_at`.
3. Set school's `owner_person_id` to Wouter; drop the UNIQUE constraint;
   add the partial unique index; set NOT NULL.
4. Copy the household `meta/protocol.md` into both personal spaces; the
   household copy remains as an ordinary page the owner may delete.
5. Re-apply RLS policies with the split write predicate (same DDL used by
   `tests/conftest.py`).

## Tests

- Generalize the single `household` conftest fixture into a builder for
  arbitrary person/space/membership graphs; keep a two-person default so
  existing tests stay readable.
- New coverage: slug resolution and denial without membership; two shared
  spaces for one person (index, context, cache versions, `list_spaces`);
  create/invite/remove authority checks; invite → bind → onboarding
  (personal space + starter pages appear once, idempotently); sharing
  disclosure enumerates members; viewer-row adversarial read/write split;
  typo-repair removal of an unbound invitee.

## Docs to update

`docs/spec.md` (revision note pointing here), `docs/how-it-works.html`
("three spaces… four membership rows, ever" prose and diagrams),
`docs/runbook.md` (go-live checks reference slugs, invite flow replaces the
hand-seeded allowlist steps), `README.md` framing.

## Out of scope

Invite links or codes; role parameter on `invite` (viewers stay dormant);
space→space or shared→personal sharing; renaming or deleting spaces;
transferring ownership; leaving a space voluntarily; web UI for any of this.
Each is additive on this design when the day comes.
