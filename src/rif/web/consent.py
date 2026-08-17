"""The reef-branded consent page for the OAuth proxy.

FastMCP's ``_show_consent_page`` performs every security-relevant step --
transaction lookup, remember-mode silent consent, CSRF issuance, cookie
handling -- and then calls a module-level ``create_consent_html`` for the
HTML string. This module supplies that string in reef's design and rebinds
the one name; no protocol logic is copied or overridden. The signature must
match FastMCP's exactly (a test compares them), and the form must post the
exact field names ``_submit_consent`` reads: ``txn_id``, ``csrf_token``,
and ``action`` valued ``approve`` or ``deny``.
"""

import html as html_module
from urllib.parse import urlparse

_STYLE = """
:root {
  --ground: #fbfcfd; --panel: #f2f7f8; --hairline: #e5edf0;
  --ink: #1c2b33; --muted: #7b8a92;
  --accent: #0d9488; --accent-deep: #0b6b62; --accent-soft: #e7f9f4;
  --cta-ink: #ffffff; --field: #ffffff;
  --warn-bg: #fff7ed; --warn-border: #fed7aa; --warn-ink: #7c4a12;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ground: #0d1a20; --panel: #0f2129; --hairline: #1c333d;
    --ink: #e2f1f5; --muted: #8fb0ba;
    --accent: #38bdd8; --accent-deep: #7ce3d3; --accent-soft: #123a35;
    --cta-ink: #06282e; --field: #0f2129;
    --warn-bg: #2a2013; --warn-border: #7c4a12; --warn-ink: #fbbf24;
  }
}
@font-face {
  font-family: "Nunito";
  src: url("/site/nunito-latin.woff2") format("woff2");
  font-weight: 200 1000; font-display: swap;
}
* { box-sizing: border-box; margin: 0; }
body {
  font-family: "Nunito", -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, sans-serif;
  background: var(--ground); color: var(--ink);
  min-height: 100vh; display: grid; place-items: center; padding: 24px;
}
main { width: min(30rem, 100%); }
.brand {
  display: flex; align-items: center; gap: 10px; margin-bottom: 20px;
  font-weight: 800; letter-spacing: 0.14em; font-size: 0.8rem;
  text-transform: uppercase; color: var(--muted);
}
.brand img { width: 28px; height: 28px; }
h1 { font-size: 1.45rem; line-height: 1.3; margin-bottom: 4px; }
h1 em { color: var(--accent-deep); font-style: normal; }
.lead { color: var(--muted); font-size: 0.92rem; margin-bottom: 20px; }
.card {
  background: var(--panel); border: 1px solid var(--hairline);
  border-radius: 14px; padding: 18px;
}
.dest {
  background: var(--field); border: 1px solid var(--hairline);
  border-radius: 10px; padding: 12px 14px; margin-bottom: 14px;
}
.dest small {
  display: block; letter-spacing: 0.1em; text-transform: uppercase;
  font-size: 0.66rem; color: var(--muted); margin-bottom: 4px;
}
.dest code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.84rem; word-break: break-all;
}
.warn {
  background: var(--warn-bg); border: 1px solid var(--warn-border);
  color: var(--warn-ink); border-radius: 10px; padding: 10px 14px;
  font-size: 0.84rem; margin-bottom: 14px;
}
.verified {
  background: var(--accent-soft); border: 1px solid var(--accent);
  color: var(--accent-deep); border-radius: 10px; padding: 10px 14px;
  font-size: 0.84rem; margin-bottom: 14px;
}
.allow-head {
  letter-spacing: 0.1em; text-transform: uppercase; font-size: 0.66rem;
  color: var(--muted); margin-bottom: 8px;
}
ul.caps { list-style: none; display: grid; gap: 8px; margin-bottom: 16px; }
ul.caps li {
  background: var(--field); border: 1px solid var(--hairline);
  border-radius: 10px; padding: 10px 14px; font-size: 0.86rem;
}
ul.caps b { display: block; font-size: 0.9rem; }
ul.caps span { color: var(--muted); }
.actions { display: grid; gap: 8px; }
button {
  font: inherit; font-weight: 700; border-radius: 10px; padding: 12px;
  cursor: pointer; border: 1px solid var(--hairline);
}
.approve { background: var(--accent); color: var(--cta-ink); border: 0; }
.deny { background: transparent; color: var(--muted); }
footer {
  margin-top: 16px; text-align: center; font-size: 0.78rem;
  color: var(--muted);
}
footer a { color: var(--accent-deep); }
"""

_CAPABILITIES = [
    (
        "Read everything you can see",
        (
            "every page, file and image in your personal cove and every "
            "shared cove you belong to"
        ),
    ),
    (
        "Write and delete",
        (
            "create, edit and delete pages and files in those coves, as "
            "everyone in them will see"
        ),
    ),
    (
        "Manage coves and membership",
        (
            "create and rename coves, invite people by email, remove "
            "members, leave or delete a cove"
        ),
    ),
    (
        "Export everything",
        "a full copy of any cove, including history and files",
    ),
]


def create_consent_html(
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    txn_id: str,
    csrf_token: str,
    client_name: str | None = None,
    title: str = "Application Access Request",
    server_name: str | None = None,
    server_icon_url: str | None = None,
    server_website_url: str | None = None,
    client_website_url: str | None = None,
    csp_policy: str | None = None,
    is_cimd_client: bool = False,
    cimd_domain: str | None = None,
) -> str:
    """Render the reef consent page.

    Signature intentionally identical to FastMCP's
    ``oauth_proxy.ui.create_consent_html`` -- the consent handler calls it
    by keyword, so parameter names are the interface; a test pins the
    match. Parameters this design does not surface (``scopes``, ``title``,
    ``server_website_url``, ``client_website_url``) are accepted and
    ignored: reef's grant is all-or-nothing, so listing OAuth scopes would
    imply a choice that does not exist. ``csp_policy`` is honored, not
    ignored -- see below.

    :param client_id: the registered client's id, shown when it has no name
    :param redirect_uri: where the authorization code will be sent
    :param scopes: OAuth scopes (unused; the capability list is fixed)
    :param txn_id: transaction id the form must post back
    :param csrf_token: CSRF token the form must post back
    :param client_name: self-asserted display name from registration
    :param title: page title override (unused; reef sets its own)
    :param server_name: accepted and unused -- the brand header always
        reads "reef", the product name, never the FastMCP server's
        internal name (``"rif"``)
    :param server_icon_url: this server's icon, if advertised
    :param server_website_url: accepted and unused (this design has no
        server-site link)
    :param client_website_url: the client's site (unused)
    :param csp_policy: the page's Content-Security-Policy. ``None`` emits
        this page's own default policy (which, unlike upstream's, adds
        ``font-src 'self'`` for the ``/site/nunito-latin.woff2`` webfont
        loaded by the inline ``<style>``); ``""`` omits the CSP meta tag
        entirely; any other string is used verbatim as the policy
    :param is_cimd_client: whether the client's identity is domain-verified
    :param cimd_domain: the verified domain when it is
    :returns: the full HTML document
    """
    esc = html_module.escape
    name = esc(client_name or client_id)
    host = esc(urlparse(redirect_uri).netloc or redirect_uri)
    icon = f'<img src="{esc(server_icon_url)}" alt="" />' if server_icon_url else ""
    policy = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
        "font-src 'self'; base-uri 'none'"
        if csp_policy is None
        else csp_policy
    )
    csp_meta = (
        f'<meta http-equiv="Content-Security-Policy" content="{esc(policy)}" />'
        if policy
        else ""
    )
    if is_cimd_client and cimd_domain:
        identity = (
            f'<div class="verified">This app’s identity is verified '
            f"as <b>{esc(cimd_domain)}</b>.</div>"
        )
    else:
        identity = (
            f'<div class="warn">reef has not verified that this app is '
            f"“{name}” — the name comes from the app "
            f"itself. Only continue if you recognise the destination "
            f"above.</div>"
        )
    rows = "".join(
        f"<li><b>{esc(head)}</b><span>{esc(detail)}.</span></li>"
        for head, detail in _CAPABILITIES
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
{csp_meta}
<title>Authorize {name} · reef</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
  <div class="brand">{icon}<span>reef</span></div>
  <h1><em>{name}</em> wants to access your reef</h1>
  <p class="lead">Approving sends you to sign in; your memory is only
  reachable with your account.</p>
  <div class="card">
    <div class="dest">
      <small>Your authorization code will be sent to</small>
      <code>{host}</code>
    </div>
    {identity}
    <div class="allow-head">This will allow {name} to</div>
    <ul class="caps">{rows}</ul>
    <form method="POST" action="">
      <input type="hidden" name="txn_id" value="{esc(txn_id)}" />
      <input type="hidden" name="csrf_token" value="{esc(csrf_token)}" />
      <div class="actions">
        <button type="submit" name="action" value="approve"
                class="approve">Approve and sign in</button>
        <button type="submit" name="action" value="deny"
                class="deny">Deny</button>
      </div>
    </form>
  </div>
  <footer>By approving you agree to reef’s
    <a href="/privacy">privacy policy</a>.</footer>
</main>
</body>
</html>"""


def install_consent_page() -> None:
    """Point FastMCP's consent handler at reef's renderer.

    ``_show_consent_page`` calls ``create_consent_html`` through its own
    module namespace, so rebinding that one attribute swaps the page while
    leaving every security step FastMCP's. Idempotent; called from the
    OAuth-proxy branch of ``rif.server._build_auth``.
    """
    from fastmcp.server.auth.oauth_proxy import consent as consent_module

    consent_module.create_consent_html = create_consent_html
