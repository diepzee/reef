"""The reef consent page: right contract, honest content, safe escaping."""

import inspect

from fastmcp.server.auth.oauth_proxy import consent as consent_module
from fastmcp.server.auth.oauth_proxy import ui

from reef.web.consent import create_consent_html, install_consent_page


def _render(**overrides) -> str:
    """Render the page with plausible defaults, overridable per test."""
    kwargs = {
        "client_id": "client-abc",
        "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
        "scopes": ["openid", "email"],
        "txn_id": "txn-1",
        "csrf_token": "csrf-tok",
        "client_name": "Claude",
        "server_name": "rif",
        "server_icon_url": None,
        "server_website_url": "https://reefwith.me",
        "client_website_url": None,
        "csp_policy": None,
        "is_cimd_client": False,
        "cimd_domain": None,
    }
    kwargs.update(overrides)
    return create_consent_html(**kwargs)


def test_signature_matches_fastmcp_exactly():
    """A fastmcp upgrade that changes the render contract must fail here.

    The renderer is called positionally-by-keyword from inside FastMCP's
    consent handler; parameter names and order are the whole interface.
    """
    ours = list(inspect.signature(create_consent_html).parameters)
    theirs = list(inspect.signature(ui.create_consent_html).parameters)
    assert ours == theirs


def test_install_rebinds_the_consent_module():
    """After install, FastMCP's consent handler renders reef's page."""
    original = consent_module.create_consent_html
    try:
        install_consent_page()
        assert consent_module.create_consent_html is create_consent_html
    finally:
        consent_module.create_consent_html = original


def test_form_contract_is_fastmcps():
    """txn_id, csrf_token, and approve/deny action fields, verbatim.

    These names are read by FastMCP's _submit_consent; get one wrong and
    Approve silently stops working while the page still looks perfect.
    """
    html = _render()
    assert 'name="txn_id" value="txn-1"' in html
    assert 'name="csrf_token" value="csrf-tok"' in html
    assert 'name="action" value="approve"' in html
    assert 'name="action" value="deny"' in html
    assert 'method="POST"' in html


def test_page_tells_the_truth():
    """Destination, unverified warning, and all four capability rows."""
    html = _render()
    assert "claude.ai" in html  # where the code goes
    assert "has not verified" in html  # self-asserted name
    assert "Read everything you can see" in html
    assert "Write and delete" in html
    assert "Manage coves and membership" in html
    assert "Export everything" in html


def test_brand_header_always_reads_reef():
    """The header names the product, never the FastMCP server's internal name.

    FastMCP's server is named "rif" -- server_name would carry that
    verbatim into production if it leaked into the brand header.
    """
    html = _render(server_name="rif")
    assert ">reef</span>" in html
    assert ">rif</span>" not in html


def test_client_name_is_escaped():
    """A hostile client_name registered via DCR must not become markup."""
    html = _render(client_name="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_cimd_client_shows_verified_domain_instead_of_warning():
    """A CIMD-verified client earns its domain badge, not the warning."""
    html = _render(is_cimd_client=True, cimd_domain="claude.ai")
    assert "has not verified" not in html
    assert "claude.ai" in html


def test_both_palettes_are_defined():
    """Light and dark are both present; the page follows the OS theme."""
    html = _render()
    assert "prefers-color-scheme: dark" in html


def test_default_csp_covers_the_webfont():
    """The default policy is emitted, and includes font-src for Nunito."""
    html = _render()
    assert 'http-equiv="Content-Security-Policy"' in html
    assert "font-src &#x27;self&#x27;" in html


def test_empty_csp_policy_omits_the_meta_tag():
    """An explicit empty string disables CSP entirely, as FastMCP allows."""
    html = _render(csp_policy="")
    assert "Content-Security-Policy" not in html


def test_custom_csp_policy_is_used_verbatim():
    """A caller-supplied policy replaces the default outright.

    The policy is still run through ``html.escape`` for the attribute, so
    single quotes come out as ``&#x27;`` -- the same text a browser decodes
    back to ``'`` when it parses the ``content`` attribute.
    """
    html = _render(csp_policy="default-src 'self'")
    assert 'content="default-src &#x27;self&#x27;"' in html
