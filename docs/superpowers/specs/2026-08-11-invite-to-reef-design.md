# Invite to reef — design

**Date:** 2026-08-11
**Status:** approved, ready to implement

## The problem

reef is invitation-only by construction. `principal_from_claims`
(`src/rif/auth.py:26-46`) admits a subject only if a `persons` row already
carries their verified email, and the comment says it outright: *"invitation-only,
never open signup."*

Two things are missing around that model.

**There is no way to invite someone to reef.** `invite()`
(`src/rif/spaces.py:171-195`) begins with `_owned_shared_space()` and
unconditionally creates a `Membership`. Every invite is therefore *"join this
specific cove"*, carrying the disclosure the function itself generates:
*"will permanently see everything in {slug}, past and future — there is no
un-sharing what they read."* Correct for a household member. Far too heavy to
send a friend who merely asked what reef is. Growth is meant to happen through
members inviting people they know, and that cannot require permanently exposing
a cove each time.

**An uninvited arrival hits a wall with no explanation.** `routes_auth.py:51-54`
serves a two-sentence bare string with no markup, no styling and no branding.
Its copy is also stale: it says *"rif"* and *"Ask the person who runs your
space"*, matching neither the reef rename nor the invite-only-network model.

## What we are building

1. A way to allowlist someone **without** granting access to any cove. They land
   in their own personal space, which `ensure_personal_space` (`auth.py:45`)
   already creates on first sign-in. Nothing is disclosed, so nothing can be
   regretted.
2. A **budget** — 5 new allowlist entries per member per rolling 30 days —
   because with no operator approving anything, this is the only spam control.
3. A **branded invite-only page** that explains the model and names the email
   that was rejected.

Explicitly **not** building: a request-an-invite form, a waitlist, an operator
or admin role, or any email sending. reef has no mail infrastructure and
`reefwith.me` has no MX/SPF/DKIM/DMARC. Invites are relayed by the inviter, the
same way `invite()` already works.

## Architecture

A new module, `src/rif/invitations.py`, owns exactly one idea: **an email
becomes an allowlist entry, if the inviter can afford it.**

```
allowlist(inviter, email, display_name) -> (Person, created: bool)
```

It lowercases the email and returns any existing person untouched. Only when it
would *create* a row does it consult the budget.

`spaces.invite()` loses its person-creation block (`spaces.py:173-180`) and
calls `allowlist()`, then does its membership work unchanged.
`invitations.invite_to_reef()` calls `allowlist()` and stops.

### Why the budget lives here and not on an endpoint

If the limit applied only to the new path it would be bypassable in seconds:
create a junk cove and invite a hundred people into it. `invite()` mints a
`persons` row for every unknown email, which is the same allowlist entry through
a different door. The protected resource is *allowlist entries*, so exactly one
function may create them and the check lives inside it.

This also yields a useful property: **inviting an already-known email costs
nothing.** Adding an existing member to a sixth cove is not an invite in the
sense that matters, so ordinary household use never approaches the ceiling. The
budget only bites when genuinely new people are brought in — precisely the spam
vector.

### Budget semantics

| Decision | Choice | Rationale |
|---|---|---|
| Ceiling | 5 | Product decision |
| Window | Rolling 30 days | No burst at midnight on the 1st; explainable in an error |
| Counted | New `persons` rows where `invited_by_person_id = inviter` | The protected resource |
| Not counted | Emails already present | See above |
| Storage | None — derived by `COUNT(*)` | Cannot drift; needs no reset job |
| Scope | Per inviter | Product decision |

No new table and no counter column. `Person` already carries
`invited_by_person_id` and `created_at` (`models.py:78-79`), so the budget is a
query over rows that must exist anyway.

RLS is not an obstacle: it covers only `pages`, `attachments`, `revisions` and
`promotions` (`rls.py:104,157`). `persons` has no row-level security, so the
`rif_app` role can count every row. **If `persons` ever gains an RLS policy,
this count silently returns 0 and the limit stops enforcing** — a test asserts
the ceiling to catch that.

### The Piccolo timestamp trap

`created_at` is `Timestamp` (`models.py:79`), which maps to `timestamp without
time zone`. Comparing it against an aware `datetime.now(UTC)` either errors or
compares wrongly. The window boundary must be constructed naive. This goes in
the code as a comment, not only in this spec — it is the same family of gotcha
as the raw-SQL enum casing that has bitten this project before.

## Surfaces

**MCP tool `invite_to_reef(email, display_name=None)`** — added, never modifying
`invite`, so existing connectors keep working. Since nothing sends mail, it
returns text the inviter relays:

```
{"email": …, "already_known": bool, "invites_left": int,
 "next_step": "Tell them to go to {RIF_BASE_URL} and sign in with this address."}
```

**Web endpoint `POST /api/invites`** — deliberately not under
`/api/spaces/{space}/…`, because this invite belongs to no space. Returns 429
with the unlock date when the budget is spent.

**Web UI** — an invite control in the app shell, separate from `MembersSheet`,
which is space-scoped and stays as it is. Member-driven growth cannot depend on
the inviter being an MCP user.

**`site/invite-only.html`** — replaces the bare string. Shares the marketing
site's Nunito and palette. Shows the rejected email, **HTML-escaped**: the value
comes from verified OIDC claims rather than raw input, but reflecting an
identity string into a page is not somewhere to reason about whether escaping is
needed.

## Error handling

`InviteBudgetExceeded` joins the existing vocabulary (`SpaceError`,
`PromotionError`, `AccessDenied`). Its message names **when the next invite
unlocks** — oldest-of-the-five plus 30 days — turning a dead end into a date.

`invite()` can now raise it too, so both callers must handle it. The web layer
maps it to **429** rather than 400: the request is well-formed and would
succeed later.

`MembersSheet.tsx:128-133` already renders `err.detail ?? err.message` for any
`ApiError`, so the unlock-date message surfaces there with no change — the
generic *"could not send the invite"* is only the non-`ApiError` fallback.
Verified rather than assumed during implementation.

## Accepted risks

**Concurrent invites can exceed the ceiling.** Two requests can both pass the
check and both insert, yielding 6. Closing it needs `SELECT … FOR UPDATE` on the
inviter's row or a database constraint. For a 5-per-month limit on a household
product the fix costs more than the harm. Revisit if reef ever opens to the
public.

## Testing

Real PostgreSQL, not mocks: `created_at` is filled server-side by
`TimestampNow()`, so the window arithmetic and the naive-timestamp trap only
prove out against a real server. A mocked repository would pass while production
silently never enforced the limit.

- Budget boundary — 5th succeeds, 6th refused
- Window edge — a row created 31 days ago does not count
- Known email is free — at budget 5, inviting an existing person into a cove works
- **Both doors share the budget** — 5 cove invites, then `invite_to_reef` refused
- Denied page renders and escapes the reflected email

The fourth is the test that would have caught the bypass.
