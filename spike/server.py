"""Spike: prove a FastMCP remote server satisfies the Claude connector flow.

One tool, no data. Auth is WorkOS AuthKit via FastMCP's ``AuthKitProvider``,
the documented remote-auth path that handles Dynamic Client Registration
(DCR) for us — required because the Claude connector registers itself as an
OAuth client at connect time rather than using a pre-registered client id.
``AuthKitProvider`` acts as a resource server only (WorkOS runs the actual
authorize/token flow), so no client secret is needed on this side.

Required environment:

- ``WORKOS_AUTHKIT_DOMAIN``: the AuthKit domain, e.g.
  ``https://your-app.authkit.app``.
- ``RIF_BASE_URL``: the public root URL this server is reachable at once
  deployed (no path), e.g. ``https://rif-production.up.railway.app``.
  FastMCP derives the OAuth callback and protected-resource metadata URLs
  from this.
- ``PORT``: provided by Railway at deploy time; defaults to 8000 locally.

See ``spike/NOTES.md`` for the full recipe: WorkOS dashboard settings,
redirect URLs, and the results of connecting from claude.ai and the mobile
apps (Task 1 Steps 2-4, run by a human against a live deployment).
"""

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.workos import AuthKitProvider

auth = AuthKitProvider(
    authkit_domain=os.environ["WORKOS_AUTHKIT_DOMAIN"],
    base_url=os.environ["RIF_BASE_URL"],
)

mcp = FastMCP("rif-spike", auth=auth)


@mcp.tool
def whoami() -> dict:
    """Return the authenticated identity, proving the auth chain works."""
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    return {"claims": dict(token.claims)}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), path="/mcp")
