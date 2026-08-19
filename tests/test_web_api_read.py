"""Read API: membership slicing, page fetch, 401s."""

import json
import uuid
from io import BytesIO
from zipfile import ZipFile

from conftest import _login

from reef.db import transaction_scope
from reef.pages import save_page
from reef.web.session import seal

# Fixtures `api` and `world` live in tests/conftest.py, shared with
# test_web_api_write.py.


async def _post(client, path: str, body: dict):
    """POST with the CSRF header, as the SPA's fetch client does.

    :param client: the HTTP client
    :param path: the API path
    :param body: the JSON body
    :returns: the response
    """
    return await client.post(path, json=body, headers={"x-rif-csrf": "1"})


async def test_unauthenticated_index_is_401(api):
    """An unauthenticated request to /api/index is rejected with 401."""
    response = await api.get("/api/index")
    assert response.status_code == 401


async def test_orphaned_session_is_401(api):
    """A validly-signed cookie for a since-deleted person yields 401.

    The cookie's signature checks out and its person id is well-formed, but
    no ``persons`` row backs it -- as happens when the person was deleted
    after the cookie was issued. The wrapper must reject it rather than let
    a phantom principal reach a handler that dereferences a ``None`` lookup.
    """
    token = seal(uuid.uuid4(), "ghost@x.com", secret="test-secret")
    api.cookies.set("rif_session", token)
    me = await api.get("/api/me")
    assert me.status_code == 401
    assert me.json() == {"error": "unauthenticated"}
    index = await api.get("/api/index")
    assert index.status_code == 401
    assert index.json() == {"error": "unauthenticated"}


async def test_index_is_sliced_per_person(api, world):
    """The index only lists spaces the logged-in person is a member of."""
    _alice, bob, _ = world
    _login(api, bob)
    response = await api.get("/api/index")
    assert response.status_code == 200
    aliases = {space["alias"] for space in response.json()["spaces"]}
    assert aliases == {"personal", "team"}


async def test_me(api, world):
    """/api/me reports the logged-in person's identity."""
    alice, _, _ = world
    _login(api, alice)
    body = (await api.get("/api/me")).json()
    assert body["email"] == "alice@x.com"
    assert body["display_name"] == "Alice"


async def test_sliding_renewal_preserves_sid(api, world):
    """The per-request cookie renewal carries the AuthKit sid forward."""
    from reef.web.session import unseal

    alice, _, _ = world
    token = seal(alice.id, alice.email, secret="test-secret", sid="ses_abc")
    api.cookies.set("rif_session", token)
    response = await api.get("/api/me")
    assert response.status_code == 200
    renewed = unseal(response.cookies["rif_session"], secret="test-secret")
    assert renewed is not None
    assert renewed.sid == "ses_abc"


async def test_get_page_and_404(api, world):
    """A page fetch returns the body; missing paths and foreign spaces 404."""
    from reef.access import Principal

    alice, bob, _ = world
    async with transaction_scope():
        await save_page(
            Principal(person_id=alice.id, email=alice.email),
            "team",
            "notes/plan.md",
            "# Plan\n\nThe plan summary.\n",
            message="seed",
        )
    _login(api, bob)
    page = (await api.get("/api/pages/team/notes/plan.md")).json()
    assert page["body"].startswith("# Plan")
    assert page["version"] == 1
    assert page["last_editor"] == "Alice"
    missing = await api.get("/api/pages/team/notes/absent.md")
    assert missing.status_code == 404
    foreign = await api.get("/api/pages/other-space/notes/plan.md")
    assert foreign.status_code == 404


async def test_me_sets_secure_session_cookie_by_default(api, world):
    """An authenticated response's renewed session cookie is Secure by default.

    Regression test: ``secure`` used to be derived from
    ``request.url.scheme``, which is always ``"http"`` behind Railway's
    TLS-terminating proxy and so never actually set ``Secure`` in
    production. It must instead be tied to the deploy mode.
    """
    alice, _, _ = world
    _login(api, alice)
    response = await api.get("/api/me")
    set_cookie = next(
        h
        for h in response.headers.get_list("set-cookie")
        if h.startswith("rif_session=")
    )
    assert "Secure" in set_cookie


async def test_me_omits_secure_cookie_with_dev_insecure(monkeypatch, api, world):
    """RIF_DEV_INSECURE=1 drops Secure from the renewed session cookie."""
    monkeypatch.setenv("RIF_DEV_INSECURE", "1")
    alice, _, _ = world
    _login(api, alice)
    response = await api.get("/api/me")
    set_cookie = next(
        h
        for h in response.headers.get_list("set-cookie")
        if h.startswith("rif_session=")
    )
    assert "Secure" not in set_cookie


async def test_get_image_missing_key_is_404_no_s3(api, world):
    """A nonexistent attachment key 404s without ever constructing S3.

    ``get_attachment`` returns ``None`` on a metadata-only lookup, and the
    handler must check that before building an ``S3ObjectStore`` -- so this
    path needs no S3 credentials configured, real or fake.
    """
    alice, _bob, _team = world
    _login(api, alice)
    response = await api.get("/api/images/team/nonexistent-key")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


async def test_get_image_unauthenticated_is_401(api):
    """An unauthenticated image fetch is rejected before any lookup."""
    response = await api.get("/api/images/team/nonexistent-key")
    assert response.status_code == 401
    assert response.json() == {"error": "unauthenticated"}


async def test_file_route_is_the_general_alias(api, world):
    """The file route applies the same authenticated metadata lookup."""
    alice, _bob, _team = world
    _login(api, alice)
    response = await api.get("/api/files/team/nonexistent-key")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


async def test_scoped_exports_and_full_dump_download(api, world):
    """Both portable formats and the comprehensive dump are downloadable."""
    from reef.access import Principal

    alice, _bob, _team = world
    async with transaction_scope():
        await save_page(
            Principal(person_id=alice.id, email=alice.email),
            "team",
            "notes.md",
            "Export me.",
            message="seed",
        )
    _login(api, alice)

    markdown = await _post(api, "/api/export", {"scope": "team", "format": "markdown"})
    assert markdown.status_code == 200
    assert markdown.headers["content-type"] == "application/zip"
    assert "reef-team-markdown.zip" in markdown.headers["content-disposition"]
    with ZipFile(BytesIO(markdown.content)) as archive:
        assert "coves/team/pages/notes.md" in archive.namelist()

    current_json = await _post(api, "/api/export", {"scope": "team", "format": "json"})
    assert current_json.status_code == 200
    assert current_json.json()["coves"][0]["pages"][0]["body"] == "Export me."

    dump = await _post(api, "/api/export/dump", {})
    assert dump.status_code == 200
    assert "reef-my-data.zip" in dump.headers["content-disposition"]
    with ZipFile(BytesIO(dump.content)) as archive:
        assert (
            json.loads(archive.read("manifest.json"))["format"] == "reef-full-data-dump"
        )


async def test_export_rejects_bad_format_and_foreign_scope(api, world):
    alice, _bob, _team = world
    _login(api, alice)
    assert (await _post(api, "/api/export", {"format": "xml"})).status_code == 400
    assert (
        await _post(api, "/api/export", {"scope": "secret", "format": "json"})
    ).status_code == 404


async def test_the_export_routes_refuse_a_request_without_the_csrf_header(api, world):
    """They are POSTs precisely so a cross-origin navigation cannot reach them.

    A GET here was a drive-by download of the reader's whole reef: the
    attacker never sees the bytes, but the file lands on the victim's disk,
    and a ``Lax`` session cookie rides along on that kind of navigation.
    """
    alice, _bob, _team = world
    _login(api, alice)

    for path in ("/api/export", "/api/export/dump"):
        # No CSRF header: what a cross-origin form or navigation could manage.
        assert (await api.post(path, json={})).status_code == 403
        # And the old link shape is simply gone.
        assert (await api.get(path)).status_code == 405


async def test_logout_revokes_a_stolen_cookie(api, world):
    """Deleting the cookie is not logging out.

    The cookie is a signed bearer token, so a copy taken beforehand used to
    keep working -- renewing itself on every request, with no way for the
    person who pressed the button to stop it.
    """
    alice, _bob, _team = world
    _login(api, alice)
    stolen = api.cookies["rif_session"]
    assert (await api.get("/api/me")).status_code == 200

    out = await api.post("/api/auth/logout", headers={"x-rif-csrf": "1"})
    assert out.status_code == 200

    api.cookies.set("rif_session", stolen)
    assert (await api.get("/api/me")).status_code == 401


async def test_a_session_sealed_with_a_stale_epoch_is_refused(api, world, seed):
    """The revocation primitive, exercised directly: a cookie sealed before
    the bump is dead even though its signature and expiry are both fine."""
    alice, _bob, _team = world
    _login(api, alice)
    assert (await api.get("/api/me")).status_code == 200

    await seed.execute(
        "UPDATE persons SET session_epoch = session_epoch + 1 WHERE id = $1",
        alice.id,
    )
    assert (await api.get("/api/me")).status_code == 401


async def test_a_surviving_session_is_renewed_without_resetting_its_chain(api, world):
    """The renewal has to carry ``iat`` forward, or the absolute ceiling
    restarts on every request and never arrives."""
    from reef.web.session import unseal

    alice, _bob, _team = world
    _login(api, alice)
    before = unseal(api.cookies["rif_session"], secret="test-secret")
    assert before is not None and before.issued_at is not None

    response = await api.get("/api/me")
    assert response.status_code == 200
    # Off the response, not the jar: the jar accumulates same-name cookies
    # across requests and refuses to pick one.
    after = unseal(response.cookies["rif_session"], secret="test-secret")

    assert after is not None
    assert after.issued_at == before.issued_at
    assert after.expires_at >= before.expires_at
