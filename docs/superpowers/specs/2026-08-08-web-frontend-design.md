# Web frontend — design

Written 8 Aug 2026. Adds a browser UI to rif — the first surface that is
not an MCP client. Nothing in `docs/spec.md`'s data model, access model, or
tool surface changes; this document only adds a second door into the same
rooms.

## Purpose

Let space members — chiefly non-technical people on their phones — see their
spaces and pages, edit pages with a minimal markdown editor, and (for a
space's owner) run the space: create spaces, invite people by email, see and
remove members. Everything the frontend can do, the MCP tools can already
do; the frontend is a convenience surface, never a privileged one.

## What stays true

- **RLS is the enforced boundary.** Browser requests resolve to the same
  `Principal` and run through the same `arm`-then-query machinery as MCP
  tool calls. A frontend bug fails closed at Postgres.
- **Invitation-only, never open signup.** Browser sign-in resolves through
  the existing `principal_from_claims`: same subject binding, same
  allowlist, same first-sign-in onboarding of a personal space.
- **The index cannot drift.** It is computed per-person, per-call from the
  live pages (`build_index`); a page's one-line description is derived at
  read time from its body's first prose line (`_summary`). Human edits
  cannot structurally desync it — only degrade a description's *quality*,
  which the editor mitigates (see Editor).
- **`meta/` pages are protected.** Protocol and persona render read-only in
  the browser; no `allow_protected` write path exists in the API.

## Architecture

One repo, one Railway service, one process.

- **`frontend/`** — a Bun + React + TypeScript single-page app. Bun is the
  package manager, dev server, bundler (`bun build` handles TS/JSX/CSS
  natively), and test runner. No Node, no Vite.
- **Serving** — a multi-stage Dockerfile step (`oven/bun`) builds the
  bundle into static files; the existing FastMCP/Starlette app serves them
  at `/app/*` and exposes a JSON API at `/api/*` via FastMCP custom routes.
  `/mcp` is untouched.
- **API handlers are thin.** Each endpoint parses the request, calls the
  existing domain function (`pages.py`, `spaces.py`, `context.py`,
  `attachments.py`) with the resolved `Principal`, and shapes the JSON
  response. No business logic lives in the handlers.

## Auth: browser session flow

The MCP path keeps its bearer-token flow through `AuthKitProvider`. The
browser gets a parallel, standard flow against the same AuthKit domain:

1. `/app` without a session redirects to AuthKit's hosted login
   (authorization-code + PKCE). Requires two additions to the environment:
   the WorkOS environment client id, and a registered redirect URI
   `{RIF_BASE_URL}/api/auth/callback`.
2. The callback exchanges the code, verifies the token claims (issuer,
   audience, expiry, `email_verified is True`), and resolves the person
   through the existing `principal_from_claims`. Unknown identities are
   denied exactly as on the MCP path.
3. On success the server sets a signed session cookie — HttpOnly, Secure,
   SameSite=Lax, 7-day sliding expiry — containing the person id and
   email. Every `/api/*` request resolves its `Principal` from this
   cookie.
4. CSRF: mutating requests must carry a custom header
   (`X-Rif-Frontend: 1`); combined with SameSite=Lax this blocks
   cross-origin form posts. The cookie is never readable by JS.
5. Logout clears the cookie.

Auth-touching code: the security auditor reviews this chunk before merge.

## API surface

Every endpoint maps 1:1 onto an existing function; errors map onto HTTP
status codes (`AccessDenied` → 403 or 404 per the existing
existence-oracle rules, `VersionConflict` → 409, `ProtectedPath` → 403,
`SpaceError` → 400).

| Endpoint | Backs onto |
|---|---|
| `GET /api/me` | session principal (`display_name`, email) |
| `GET /api/index` | `build_index` — spaces, pages, attachments |
| `GET /api/pages/{space}/{path}` | `get_page` — body, title, tags, version |
| `PUT /api/pages/{space}/{path}` | `save_page` — body, title, tags, message, `expected_version` |
| `POST /api/spaces` | `create_space` |
| `GET /api/spaces/{s}/members` | `member_names` |
| `POST /api/spaces/{s}/invites` | `invite` (owner-only, enforced in `spaces.py` as today) |
| `DELETE /api/spaces/{s}/members/{email}` | `remove_member` (owner-only) |
| `GET /api/images/{space}/{key}` | attachment fetch, so pages can render images |

Page paths contain slashes; the route pattern accepts them
(`{path:path}`). Space is always the alias (`personal` or slug), resolved
per-principal exactly as at the tool boundary.

## Frontend views

Mobile-first; five screens behind a client-side router.

1. **Home** — the person's spaces, personal first (their per-person index
   slice, nothing more); a "new space" action.
2. **Space** — page list (title, description, updated); for the owner, a
   members panel with invite-by-email and remove; attachments listed.
3. **Page** — markdown rendered client-side with markdown-it, sanitized
   with DOMPurify (page bodies are untrusted data); images resolved
   through `/api/images/…`; an Edit action. `meta/` pages show no Edit.
4. **Editor** — title, tags, and a plain `<textarea>` with a preview
   toggle; an optional "why" message on save. A visible hint marks the
   first prose line as the page's index description and previews it live.
   Saves send `expected_version`; a 409 tells the user someone else saved
   meanwhile and offers reload-and-reapply (no auto-merge).
5. **New page** — path, title, body; same editor component.

## Error handling

- API errors return `{error, detail}` JSON; the SPA surfaces them as
  human-readable notices, not toasts that vanish.
- A 401 from any API call routes to login (session expired).
- Version conflicts are first-class UX (see Editor), not alerts.
- The SPA never retries mutations automatically.

## Testing

- **Backend (mandatory):** pytest against real Postgres, alongside the
  existing suite — session auth (valid, expired, tampered cookie), RLS
  isolation between two members with different space sets, version
  conflict through the API, owner-only admin enforcement, CSRF header
  requirement, `meta/` write refusal.
- **Frontend:** `bun test` for pure logic (API client, index-description
  preview derivation). No browser-automation suite in v1; flows are
  verified manually with Playwright before completion.

## Dev & deploy

- **Local dev:** `bun dev` serves the SPA with `/api` proxied to a local
  server. An explicit `RIF_DEV_INSECURE=1` allows localhost HTTP with the
  existing `RIF_DEV_PRINCIPAL_EMAIL` fallback; without the flag, the
  production guard (refuse HTTP without AuthKit) stands.
- **Deploy:** the existing Railway service; the Dockerfile gains a Bun
  build stage whose output the Python image copies and serves. New env
  vars: the WorkOS client id and a session-cookie signing secret.
- **Operator step:** register the callback redirect URI in the WorkOS
  dashboard.

## Out of scope (v1)

- Revision history browsing (revisions are stored; no UI reads them yet).
- Sharing/promotion flows (`prepare_to_share`/`confirm_share` stay
  Claude-only — they are conversational by design).
- Image upload from the browser (attachments render, are not created).
- Offline editing, live collaboration, auto-merge on conflict.
- Read-only (VIEWER) membership UI — dormant in the DB, dormant here.
