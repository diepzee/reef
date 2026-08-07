# Task 1 spike notes — Claude connector + OAuth

**Status: the gate passed.** The connector chain works end to end — WorkOS
AuthKit, Dynamic Client Registration, token audience, and identity binding —
and the real service went live on 6 Aug 2026. These notes are kept as the
record of what was predicted, what was wrong, and what was finally observed.

Two things this spike set out to answer are still open, both about *her* side
rather than the mechanism: the connector has not been confirmed on a phone,
and her account and tier have not been tried. See "Tier limitations" below.

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

## Routes this server exposes — CORRECTED, observed live 6 Aug

Predicted from source, and two of the three were wrong. Observed against the
deployed service:

| Path | Status |
|---|---|
| `/mcp` | 401 unauthenticated, as intended |
| `/.well-known/oauth-protected-resource/mcp` | **200** — note the `/mcp` suffix |
| `/.well-known/oauth-protected-resource` | 404 |
| `/.well-known/oauth-authorization-server` | 200 |
| `/.well-known/oauth-authorization-server/mcp` | 404 |

So protected-resource metadata is served **path-suffixed** (RFC 9728 style,
keyed to the resource path) while the authorization-server forward sits at
the root. The earlier note predicted both at the root.

This does not need configuring anywhere: the 401 advertises the right one in
its challenge header, which is how a client discovers it:

```
www-authenticate: Bearer resource_metadata="https://<base>/.well-known/oauth-protected-resource/mcp"
```

The actual authorize/token/callback exchange happens between the client
(Claude) and `{WORKOS_AUTHKIT_DOMAIN}` directly — our server is never in
that request path.

## WorkOS dashboard — CORRECTED, done 6 Aug

- **Dynamic Client Registration ships DISABLED.** It is not under
  "Applications → Configuration" as guessed; it is **Connect → Configuration
  → MCP Auth**, with separate checkboxes for DCR and Client ID Metadata
  Document. Only DCR was enabled — Claude uses it and there is no reason to
  open a second mechanism. Had this been missed, the connector would have
  failed with no obvious cause.
- **Resource Indicators are required, not optional** — see the corrected
  note above. Set to `https://rif-app-production.up.railway.app/mcp`.
- **"Return Google OAuth tokens" defaults ON when you save your own
  credentials.** Turned off: it hands the server live credentials to both
  members' entire Google accounts, which rif never uses.
- **Staging pre-enables Microsoft, GitHub and Apple on WorkOS demo
  credentials. Production does not** — Production ships with every provider
  off. Only Google was enabled there.
- **The Production AuthKit domain is not derivable from the Staging one.**
  Staging `worthy-moon-29-staging.authkit.app`; Production
  `thankful-origami-62.authkit.app`. Read it, never derive it.
- Each environment needs its own Google redirect URI. One Google OAuth
  client holds both.

## Deploy / connect steps — done 6 Aug 2026, except the phones

- [x] Step 2 — Railway: deployed. `RIF_BASE_URL` =
      `https://rif-app-production.up.railway.app`, `WORKOS_AUTHKIT_DOMAIN`
      set to the Production AuthKit domain (`thankful-origami-62.authkit.app`
      — read from the dashboard, never derived from Staging).
- [x] Step 3 — connector added and working. It went past the spike to the
      real service: `/mcp` answers authenticated tool calls, and
      `list_spaces` returns `personal` and `household` as aliases only.
- [ ] Step 4 — phones. **Still open.** Desktop is re-confirmed working as of
      7 Aug 2026, but no phone has been tried, and neither has her account or
      tier. Hers is blocked behind the runbook's Phase 2 regardless of tier:
      she is not on the allowlist, so `principal_from_claims` would deny her
      even after a successful login.

## DCR behavior observed

**Confirmed working.** Claude registered itself against WorkOS with no manual
client id or secret entered anywhere — the stored connector config is nothing
but a type and a URL:

```json
{"type": "http", "url": "https://rif-app-production.up.railway.app/mcp"}
```

The `GoogleProvider` escape hatch below was therefore never needed. It stays
documented in case AuthKit's DCR support regresses.

## Claims that arrive — answered by outcome, not by capture

The source-level caveat below turned out **not** to bite: `email` and
`email_verified` do arrive in AuthKit's access-token JWT, not only in the ID
token.

That is an inference from behavior rather than a captured payload, and the
reasoning is worth keeping because it is tight: `principal_from_claims`
(`src/rif/auth.py`) binds an unknown subject *only* when the token carries
both `email` and a truthy `email_verified`, and raises `AccessDenied`
otherwise. Every principal in production started unknown. Binding succeeded.
So both claims were present at first login.

**Not on record:** the field-by-field `claims` dict. The spike's `whoami`
output was never written down before the real server took over, so the exact
set of claims AuthKit sends — beyond the three the binding path proves —
remains uncaptured. If that ever matters (adding a provider, debugging a
failed bind), log the claims dict once from the real server rather than
re-deploying the spike.

The original caveat, kept for context: `AuthKitProvider`'s default token
verifier is a plain `JWTVerifier` that decodes AuthKit's access-token JWT and
exposes whatever claims are in it — no forced shape, no userinfo call.
Whether `email` / `email_verified` land in that JWT depends on how the AuthKit
application is configured. Contrast: the *other* WorkOS provider in this file
(`WorkOSProvider`, the proxy variant, not what we're using) explicitly calls
`/oauth2/userinfo` and normalizes `sub`, `email`, `email_verified`, `name`,
`given_name`, `family_name` — the fallback shape to reach for if AuthKit's
claims ever turn thin.

## Tier limitations found

**Still open** — requires Step 4 on her account and tier. Nothing has been
observed either way, on any phone.

The stakes are lower than when this was written: the store, the tools, the
access model and the deploy are all proven, and his own use runs over the
same connector from Claude Code. If connectors turn out to be unavailable on
her tier or missing from mobile, only *her* surface reopens (the PWA path) —
rif keeps working for him throughout.
