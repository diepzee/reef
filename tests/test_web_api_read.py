"""Read API: membership slicing, page fetch, 401s."""

import uuid

from conftest import _login

from rif.db import transaction_scope
from rif.pages import save_page
from rif.web.session import seal

# Fixtures `api` and `world` live in tests/conftest.py, shared with
# test_web_api_write.py.


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
    from rif.web.session import unseal

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
    from rif.access import Principal

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
