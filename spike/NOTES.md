# Task 1 spike notes — Claude connector + OAuth

**Status: Step 1 done (code + provider choice, confirmed against installed
FastMCP source). Steps 2–4 are pending** — they need a Railway deployment,
a live WorkOS account, and the mobile app on two people's phones, none of
which an agent in this worktree may touch (no `railway`/deploy commands, no
production, no another person's account/phone). Whoever runs those steps
should fill in the "PENDING" sections below and re-commit this file before
Task 6 relies on it.

## Provider choice

**WorkOS AuthKit**, via FastMCP's `AuthKitProvider`
(`fastmcp.server.auth.providers.workos.AuthKitProvider`), installed as part
of `fastmcp==3.4.6` (`fastmcp>=2` resolved to this).

Why this one and not `WorkOSProvider` (the other WorkOS class in the same
module): `AuthKitProvider` is a metadata-forwarding *resource server* —
it advertises AuthKit as the authorization server (RFC 9728 protected-resource
metadata + forwarded `/.well-known/oauth-authorization-server`) and lets
AuthKit run the actual authorize/token/DCR exchange with the client
directly. `WorkOSProvider` instead proxies the flow through our own server
using a single fixed upstream client id/secret, which defeats the point of
testing real DCR. The brief calls out AuthKit as the DCR-native path; the
source confirms it (`AuthKitProvider` docstring: "recommended approach for
WorkOS DCR ... allows WorkOS to handle the OAuth flow directly").

**Fallback if AuthKit disappoints**: `fastmcp.server.auth.providers.google.GoogleProvider`.
It's built on FastMCP's `OAuthProxy`, which implements *local* DCR (our
server hands out a client_id/secret pair per registering client, backed by
one fixed upstream Google OAuth app) since Google itself has no DCR. Needs
`client_id` + `client_secret` from a Google Cloud OAuth consent screen —
more setup than AuthKit, kept as the documented escape hatch only.

## Server code

`spike/server.py` — the `whoami` tool and `__main__` block are verbatim from
the plan brief. Added around them: `AuthKitProvider` construction from two
env vars, passed as `FastMCP("rif-spike", auth=auth)`. No client secret is
needed for this provider — WorkOS is the authorization server, we're only a
resource server verifying its tokens.

Confirmed locally: the module imports and constructs the `FastMCP` app and
`AuthKitProvider` without error given dummy env vars (no network call
happens at construction time — the JWKS fetch is lazy, on first token
verification).

## Exact env vars

| Var | Example | Notes |
|---|---|---|
| `WORKOS_AUTHKIT_DOMAIN` | `https://your-app.authkit.app` | From WorkOS dashboard → your AuthKit-enabled application. `https://` prefix optional — `AuthKitProvider` adds it if missing. |
| `RIF_BASE_URL` | `https://rif-production.up.railway.app` | Public root URL of the deployed server, **no path**. Drives the resource URL (`{RIF_BASE_URL}/mcp`) advertised in protected-resource metadata and the audience (`aud`) `AuthKitProvider` expects on incoming tokens. |
| `PORT` | `8000` | Set by Railway automatically at deploy time; `server.py` defaults to `8000` when unset (matches the brief's snippet). |

No client id/secret env vars — that's the whole appeal of the metadata-
forwarding provider over the proxy providers.

## WorkOS dashboard setup (human, needs a live WorkOS account)

From the `AuthKitProvider` docstring, to be done before Step 2/3:

1. Create/open a WorkOS application with AuthKit enabled; copy its AuthKit
   domain into `WORKOS_AUTHKIT_DOMAIN`.
2. Applications → Configuration → toggle **Dynamic Client Registration**
   on. This is the setting Step 4's DCR test is actually exercising.
3. The docstring also says: "Configure your FastMCP server URL as a
   callback: add your server URL to the Redirects tab ... e.g.
   `https://your-fastmcp-server.com/oauth2/callback`." **Flagged, not
   confirmed**: `AuthKitProvider.get_routes()` (read from source) only adds
   protected-resource metadata routes plus the authorization-server metadata
   forward — it registers no `/oauth2/callback` handler. That callback
   instruction reads like it may be inherited from the `WorkOSProvider`
   (proxy) docstring directly above it in the same file rather than written
   for the metadata-forwarding path. Whoever does the live setup: try
   without it first (AuthKit should redirect straight to Claude's own
   registered redirect URI, obtained via DCR); only add
   `{RIF_BASE_URL}/oauth2/callback` to WorkOS's Redirects tab if the
   connector flow fails without it, and correct this note either way once
   observed.
4. **Not optional, corrected 6 Aug:** enable Resource Indicators (RFC 8707)
   in the WorkOS dashboard and list `{RIF_BASE_URL}/mcp` as the resource. The
   docstring frames this as a nicety; the running server does not. Booting
   the built image logs, at INFO: *"AuthKit tokens will be validated against
   aud=<RIF_BASE_URL>/mcp. Configure this URL as a Resource Indicator in the
   WorkOS Dashboard."* That is the audience `AuthKitProvider` checks on every
   token, so without a matching resource every authenticated call is rejected.
   Use the URL from the log line verbatim.

## Routes this server exposes (confirmed from source, not yet hit live)

- `{RIF_BASE_URL}/mcp` — the MCP endpoint (`path="/mcp"` in `mcp.run`).
- `{RIF_BASE_URL}/.well-known/oauth-protected-resource` — RFC 9728 metadata,
  from `RemoteAuthProvider.get_routes`.
- `{RIF_BASE_URL}/.well-known/oauth-authorization-server` — AuthKit's own
  metadata document, fetched server-side and forwarded verbatim
  (`AuthKitProvider.get_routes`, the `oauth_authorization_server_metadata`
  handler).

The actual authorize/token/callback exchange happens between the client
(Claude) and `{WORKOS_AUTHKIT_DOMAIN}` directly, per the metadata above —
our server is never in that request path.

## Deploy / connect steps — PENDING (human, live)

- [ ] Step 2 — Railway: `railway init --name rif`, `railway up`,
      `railway domain`, with `WORKOS_AUTHKIT_DOMAIN` and `RIF_BASE_URL` set
      as Railway env vars (`RIF_BASE_URL` = the domain Railway assigns,
      known only after the first `railway domain`).
- [ ] Step 3 — claude.ai web, your account: add `https://<domain>/mcp` as a
      custom connector, call `whoami`, confirm your email appears in
      `claims`.
- [ ] Step 4 — phones: confirm the connector + `whoami` on your phone, then
      on your wife's account/tier on her phone.

## DCR behavior observed

**PENDING** — requires Step 3. Record here once observed: did Claude
register a client automatically against WorkOS with no manual client
id/secret entry anywhere, and did that registration succeed on first
connector-add attempt?

## Claims that arrive — PENDING, with a source-level caveat

**PENDING live confirmation.** Source-level caveat worth carrying into that
test: `AuthKitProvider`'s default token verifier is a plain `JWTVerifier`
that decodes AuthKit's access-token JWT and exposes whatever claims are
in it — no forced shape, no userinfo call. Whether `email` /
`email_verified` land in that JWT depends on how the AuthKit application is
configured (they might be ID-token-only, not access-token claims, depending
on WorkOS's setup). Contrast: the *other* WorkOS provider in this file
(`WorkOSProvider`, the proxy variant, not what we're using) explicitly calls
`/oauth2/userinfo` and normalizes `sub`, `email`, `email_verified`, `name`,
`given_name`, `family_name` — a working fallback shape to compare against if
the AuthKit JWT claims turn out thin. Record the actual `claims` dict
returned by `whoami` here once Step 3 runs.

## Tier limitations found

**PENDING** — requires Step 4 on your wife's account/tier. Per the plan: if
connectors are unavailable on her tier or on mobile, stop and reopen the
surface decision (PWA path) before Tasks 2–5 proceed — those tasks survive
either way, only the transport (Task 6) is in question.
