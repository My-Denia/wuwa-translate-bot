"""The owner-private web presentation layer.

Everything here runs in-process against the ASGI app, in the same style as
tests/test_api.py. The properties under test are the ones that make this
surface safe to expose at all:

- with the switch off it does not exist, so nothing about the API changes;
- a request that did not come through the edge is refused before it reaches
  application logic;
- no credential material ever reaches the browser;
- it spends the SAME rate limiter and the SAME call budget as the API, rather
  than a second set that would raise the deployment's aggregate ceiling;
- it enforces the same scopes the API enforces over the same pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

import httpx

from wuwaterm_api.app import create_app
from wuwaterm_api.auth import SCOPE_META, SCOPE_TRANSLATE, TOKEN_SCHEME, DeviceStore
from wuwaterm_api.settings import WEB_MOUNT_PATH, ApiSettings
from wuwaterm_api.web.app import EDGE_HEADER_NAME, SESSION_COOKIE_NAME
from wuwaterm_api.web.session import SessionStore

ROOT = Path(__file__).resolve().parents[1]

EDGE_SECRET = "edge-marker-material-for-tests-0123456789"
DEVICE_SECRET = "unguessable-material-for-tests-0123456789abcdef-web"


def run(coro):
    return asyncio.run(coro)


async def call(app, method: str, url: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://api.test"
    ) as client:
        return await client.request(method, url, **kwargs)


def edge() -> dict[str, str]:
    return {EDGE_HEADER_NAME: EDGE_SECRET}


def form(**fields) -> dict[str, str]:
    """A urlencoded body. The web layer accepts no other content type."""
    return fields


def session_cookie(app) -> dict[str, str]:
    """Load a page first, the way a browser reaching this surface does.

    A POST cannot mint a session - that restriction is what closes cross-site
    request forgery - so every POST test has to arrive holding a cookie it got
    from a GET, exactly as the owner's browser does after loading the form.
    """
    page = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert page.status_code == 200, page.status_code
    return {SESSION_COOKIE_NAME: page.cookies[SESSION_COOKIE_NAME]}


def build_web_app(tmp_path: Path, db_path: Path, *, scopes=None, **overrides):
    """An app with the web layer switched ON and a device behind it."""
    store = DeviceStore(tmp_path / "api-state" / "devices.db")
    store.initialize()
    device = store.issue("web", scopes, secret=DEVICE_SECRET)
    token = f"{TOKEN_SCHEME}.{device.device_id}.{DEVICE_SECRET}"
    defaults = dict(
        db_path=db_path,
        device_db_path=tmp_path / "api-state" / "devices.db",
        rate_limit_per_minute=100,
        llm_calls_per_minute=100,
        max_body_bytes=8192,
        request_timeout_seconds=30.0,
        web_enabled=True,
        web_device_token=token,
        web_edge_secret=EDGE_SECRET,
    )
    defaults.update(overrides)
    settings = ApiSettings(**defaults)
    app = create_app(settings, device_store=store)
    return app, store, device, token


def mounted_web_app(app):
    """The sub-application, or None when the switch left it unmounted."""
    for route in app.routes:
        if route.__class__.__name__ == "Mount" and route.path == WEB_MOUNT_PATH:
            return route.app
    return None


# --------------------------------------------------------------- the switch


def test_the_web_layer_is_absent_by_default(tmp_path, sample_db):
    """Default OFF. Not "mounted but refusing" - absent.

    This is what makes "the API is unchanged" checkable rather than argued: if
    nothing is mounted, there is no code path to reason about.
    """
    settings = ApiSettings(
        db_path=sample_db, device_db_path=tmp_path / "api-state" / "devices.db"
    )
    assert settings.web_enabled is False
    store = DeviceStore(settings.device_db_path)
    store.initialize()
    app = create_app(settings, device_store=store)
    assert mounted_web_app(app) is None
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/"))
    assert response.status_code == 404


def test_the_switch_does_not_change_the_published_api_document(tmp_path, sample_db):
    """The OpenAPI document is byte-identical with the layer on and off.

    A mounted sub-application is a Mount, not an APIRoute, so it contributes no
    schema. Pinning that here means a future change that turned the web layer
    into API routes would be caught by this test rather than by a client that
    suddenly sees new paths.
    """
    off = ApiSettings(
        db_path=sample_db, device_db_path=tmp_path / "off" / "devices.db"
    )
    DeviceStore(off.device_db_path).initialize()
    app_off = create_app(off, device_store=DeviceStore(off.device_db_path))
    app_on, _store, _device, _token = build_web_app(tmp_path, sample_db)

    doc_off = run(call(app_off, "GET", "/openapi.json")).json()
    doc_on = run(call(app_on, "GET", "/openapi.json")).json()
    assert doc_off == doc_on
    assert not [path for path in doc_on["paths"] if "wuwaterm-web" in path]


# ------------------------------------------------- criterion 5: one instance


def test_the_web_layer_spends_the_api_s_own_limiter_and_budget(tmp_path, sample_db):
    """Identity, not equality.

    Two SlidingWindowRateLimiter objects with the same limit compare equal in
    every way a test might casually check and are still two independent
    buckets. The property that matters is that there is ONE object, so this
    asserts `is`.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    web = mounted_web_app(app)
    assert web is not None
    assert web.state is app.state
    assert web.state.rate_limiter is app.state.rate_limiter
    assert web.state.llm_budget is app.state.llm_budget
    assert web.state.term_service is app.state.term_service
    assert web.state.translator is app.state.translator
    assert web.state.device_store is app.state.device_store


def test_an_established_session_re_runs_no_credential_derivation(
    tmp_path, sample_db, monkeypatch
):
    """What the shared-process cost actually is, pinned so it cannot drift.

    Sharing the rate limiter with the desktop client is an accepted cost, and
    its weight depends entirely on this: a browser request that re-verified the
    credential would spend a deliberate ~16 MiB scrypt AND one of the bounded
    admission slots the desktop client sheds 429 from, on every page view. It
    does not. Verification happens once, when the session is created; after
    that a request costs one bucket token and a cheap liveness read.

    Counted at hashlib.scrypt - the real derivation - rather than by reading
    the call graph, because the call graph is what a regression would change.
    """
    # Built BEFORE the counter is installed: issuing the device runs a
    # derivation of its own, and counting it would make the first assertion
    # below read 2 for a reason that has nothing to do with serving requests.
    app, _store, _device, token = build_web_app(tmp_path, sample_db)

    calls = []
    real_scrypt = hashlib.scrypt

    def counting_scrypt(*args, **kwargs):
        calls.append(1)
        return real_scrypt(*args, **kwargs)

    monkeypatch.setattr(hashlib, "scrypt", counting_scrypt)
    slots = []
    real_acquire = app.state.auth_slots.acquire
    monkeypatch.setattr(
        app.state.auth_slots,
        "acquire",
        lambda *a, **k: (slots.append(1), real_acquire(*a, **k))[1],
    )

    first = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert first.status_code == 200
    assert len(calls) == 1, "session creation verifies the credential exactly once"
    assert len(slots) == 1
    jar = {SESSION_COOKIE_NAME: first.cookies[SESSION_COOKIE_NAME]}

    for _ in range(10):
        later = run(
            call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge(), cookies=jar)
        )
        assert later.status_code == 200
    assert len(calls) == 1, "an established session must not re-derive"
    assert len(slots) == 1, "an established session must not take an admission slot"

    # For contrast, and to show the counter is live: the JSON API's shape
    # presents a credential on every request and pays for it every time.
    for _ in range(3):
        run(call(app, "GET", "/v1/meta", headers={"Authorization": f"Bearer {token}"}))
    assert len(calls) == 4


def test_browser_traffic_shares_one_rate_limit_bucket_with_the_api(
    tmp_path, sample_db
):
    """One device, one bucket, whichever presentation layer spends it.

    If the web layer had its own limiter the two surfaces would each get the
    configured allowance, and the deployment's real ceiling would be double the
    configured number without any setting saying so.
    """
    app, _store, _device, token = build_web_app(
        tmp_path, sample_db, rate_limit_per_minute=3
    )
    # Spend the whole allowance through the JSON API...
    for _ in range(3):
        assert (
            run(
                call(
                    app,
                    "GET",
                    "/v1/meta",
                    headers={"Authorization": f"Bearer {token}"},
                )
            ).status_code
            == 200
        )
    # ...and the browser surface, for the SAME device, is already out.
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert response.status_code == 429


# --------------------------------------- criterion 2: refused before logic


def test_a_request_without_the_edge_marker_is_refused(tmp_path, sample_db):
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/"))
    assert response.status_code == 404
    # 404, not 401 or 403: a caller that bypassed the edge learns nothing about
    # what is mounted here, including whether anything is.
    assert response.text == ""


def test_a_request_with_the_wrong_edge_marker_is_refused(tmp_path, sample_db):
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(
        call(app, "GET", f"{WEB_MOUNT_PATH}/", headers={EDGE_HEADER_NAME: "wrong"})
    )
    assert response.status_code == 404


def test_the_refusal_happens_before_the_credential_store_is_touched(
    tmp_path, sample_db
):
    """The ordering claim, made falsifiable.

    Asserting a status code proves the request was refused; it does not prove
    WHERE. This replaces the credential store with one that raises if anything
    reaches it, so a future edit that resolved the session before checking the
    edge would fail here rather than pass quietly.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)

    class Tripwire:
        def authenticate(self, *_args, **_kwargs):
            raise AssertionError("credential store reached before the edge check")

        def is_active(self, *_args, **_kwargs):
            raise AssertionError("credential store reached before the edge check")

    app.state.device_store = Tripwire()
    assert run(call(app, "GET", f"{WEB_MOUNT_PATH}/")).status_code == 404


def test_every_path_under_the_mount_refuses_identically(tmp_path, sample_db):
    """One answer for everything off-edge, or the refusal is an oracle.

    Each of these used to answer differently and each difference told a caller
    that never presented the marker something about what is mounted here:
    the router's 303 on an unguarded route, its 307 on a trailing slash, its
    405 on a known path with the wrong method, and its `Not Found` BODY on an
    unknown one. A refusal that varies is a map.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    probes = (
        ("GET", f"{WEB_MOUNT_PATH}/"),
        ("GET", f"{WEB_MOUNT_PATH}/lookup"),
        ("GET", f"{WEB_MOUNT_PATH}/lookup/"),
        ("GET", f"{WEB_MOUNT_PATH}/translate"),
        ("GET", f"{WEB_MOUNT_PATH}/translate/"),
        ("POST", f"{WEB_MOUNT_PATH}/"),
        ("POST", f"{WEB_MOUNT_PATH}/translate"),
        ("GET", f"{WEB_MOUNT_PATH}/does-not-exist"),
        ("DELETE", f"{WEB_MOUNT_PATH}/lookup"),
    )
    seen = set()
    for method, url in probes:
        response = run(call(app, method, url))
        seen.add((response.status_code, response.text))
    assert seen == {(404, "")}, seen


def test_a_trailing_slash_is_not_a_route_even_on_edge(tmp_path, sample_db):
    """Pins the SECOND of the two controls, independently of the first.

    Off-edge uniformity is enforced by the gate that refuses above the router,
    so a test that only probes without the marker passes whether or not the
    router still redirects - it is measuring the gate. This probes WITH a valid
    marker, where the router is actually reached, so it fails if
    redirect_slashes comes back on. Two controls, two assertions; otherwise the
    inner one is carried by the outer one and nothing says so.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    for url in (f"{WEB_MOUNT_PATH}/translate/", f"{WEB_MOUNT_PATH}/lookup/"):
        response = run(call(app, "GET", url, headers=edge()))
        assert response.status_code == 404, (url, response.status_code)


def test_a_non_ascii_edge_marker_is_refused_not_a_server_error(tmp_path, sample_db):
    """A caller-controlled byte must not become a 500.

    Starlette decodes header values latin-1, so a byte >= 0x80 produces a
    non-ASCII str, and secrets.compare_digest raises TypeError on those. That
    exception was neither ApiError nor the refusal type, so it escaped the
    sub-application and surfaced through the PARENT's handler: an
    unauthenticated caller got a 500 where every other bad marker gives 404,
    and drove a traceback into the API process's log once per request.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(
        call(
            app,
            "GET",
            f"{WEB_MOUNT_PATH}/",
            headers={EDGE_HEADER_NAME.encode(): b"\xff\xfe"},
        )
    )
    assert response.status_code == 404
    assert response.text == ""


def test_the_refusal_carries_the_hardening_headers(tmp_path, sample_db):
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/"))
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_a_post_without_a_session_cannot_mint_one(tmp_path, sample_db):
    """The control that actually closes cross-site request forgery.

    While any request could mint a session, an absent cookie was not a refusal
    but a trigger to create one - so a cross-site form POST still ran: the
    browser withholds the SameSite=Strict cookie, but the edge injects its
    marker on every proxied request regardless of who caused it and the browser
    attaches its cached basic-auth credentials. The attacker could not read the
    response, but could spend the model-call budget at will.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(
        call(
            app,
            "POST",
            f"{WEB_MOUNT_PATH}/translate",
            headers=edge(),
            data=form(text="今汐"),
        )
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"{WEB_MOUNT_PATH}/translate"
    # Nothing was minted, so nothing was spent.
    assert "set-cookie" not in response.headers
    # ...and a GET first still works, which is the ordinary flow.
    page = run(call(app, "GET", f"{WEB_MOUNT_PATH}/translate", headers=edge()))
    assert page.status_code == 200
    assert page.cookies[SESSION_COOKIE_NAME]


def test_a_revoked_session_is_dropped_from_the_store(tmp_path, sample_db):
    """Revocation already ended access; this is the bookkeeping half.

    A dead entry left in place holds one of the bounded slots for its whole
    TTL, and the eviction loop drops the OLDEST entry rather than the deadest -
    so under pressure a revoked session could outlive a live one.
    """
    app, store, device, _token = build_web_app(tmp_path, sample_db)
    first = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    session_id = first.cookies[SESSION_COOKIE_NAME]
    sessions = app.state.web_sessions
    assert sessions.resolve(session_id) is not None
    store.revoke(device.device_id)
    second = run(
        call(
            app,
            "GET",
            f"{WEB_MOUNT_PATH}/",
            headers=edge(),
            cookies={SESSION_COOKIE_NAME: session_id},
        )
    )
    assert second.status_code == 401
    assert sessions.resolve(session_id) is None


def test_an_unconfigured_edge_secret_refuses_everything(tmp_path, sample_db):
    """Fail closed: an operator who enabled the layer but set no edge secret
    gets a surface that refuses, never one that is open to anyone."""
    app, _store, _device, _token = build_web_app(
        tmp_path, sample_db, web_edge_secret=""
    )
    assert run(call(app, "GET", f"{WEB_MOUNT_PATH}/")).status_code == 404
    assert (
        run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge())).status_code == 404
    )


# ------------------------------------ criterion 1: nothing lands in the browser


def test_no_credential_material_reaches_the_browser(tmp_path, sample_db):
    """The token, its secret half, and the device id must appear nowhere in
    what is sent to the client - not in the body, not in a header, not in a
    cookie."""
    app, _store, device, token = build_web_app(tmp_path, sample_db)
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert response.status_code == 200
    wire = response.text + "\n" + "\n".join(
        f"{name}: {value}" for name, value in response.headers.items()
    )
    assert token not in wire
    assert DEVICE_SECRET not in wire
    assert device.device_id not in wire


def test_the_session_cookie_is_opaque_and_locked_down(tmp_path, sample_db):
    app, _store, _device, token = build_web_app(tmp_path, sample_db)
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    raw = response.headers["set-cookie"]
    assert raw.startswith(f"{SESSION_COOKIE_NAME}=")
    lowered = raw.lower()
    # HttpOnly is the one that makes "no JS-readable credential" structural.
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered
    assert f"path={WEB_MOUNT_PATH}" in lowered
    value = response.cookies[SESSION_COOKIE_NAME]
    # Opaque: it is not the token, contains no part of it, and nothing about
    # the device can be read out of it.
    assert value != token
    assert DEVICE_SECRET not in value
    assert value not in token


def test_a_forged_session_cookie_is_not_accepted(tmp_path, sample_db):
    """A guessed identifier must mint nothing. It resolves to no session, so
    the request falls through to establishing a new one from the server-held
    token - and the response must therefore carry a NEW cookie, not honour the
    forged one."""
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(
        call(
            app,
            "GET",
            f"{WEB_MOUNT_PATH}/",
            headers=edge(),
            cookies={SESSION_COOKIE_NAME: "not-a-real-session-identifier"},
        )
    )
    assert response.status_code == 200
    assert response.cookies[SESSION_COOKIE_NAME] != "not-a-real-session-identifier"


# ---------------------------------------------------------- scope enforcement


def test_translation_requires_the_translate_scope(tmp_path, sample_db):
    """A device granted only meta must not be able to translate through the
    browser, exactly as it cannot through the API."""
    app, _store, _device, _token = build_web_app(
        tmp_path, sample_db, scopes=[SCOPE_META]
    )
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/translate", headers=edge()))
    assert response.status_code == 403


def test_lookup_requires_the_meta_scope(tmp_path, sample_db):
    app, _store, _device, _token = build_web_app(
        tmp_path, sample_db, scopes=[SCOPE_TRANSLATE]
    )
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert response.status_code == 403


def test_revoking_the_device_ends_the_browser_session(tmp_path, sample_db):
    """A session must not be a way to outlive revocation."""
    app, store, device, _token = build_web_app(tmp_path, sample_db)
    first = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert first.status_code == 200
    cookie = {SESSION_COOKIE_NAME: first.cookies[SESSION_COOKIE_NAME]}
    store.revoke(device.device_id)
    second = run(
        call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge(), cookies=cookie)
    )
    assert second.status_code == 401


# ------------------------------------------------------------------ rendering


def test_a_dictionary_query_renders_its_matches(tmp_path, sample_db):
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    jar = session_cookie(app)
    response = run(
        call(
            app,
            "POST",
            f"{WEB_MOUNT_PATH}/lookup",
            headers=edge(),
            cookies=jar,
            data=form(q="共鸣者"),
        )
    )
    assert response.status_code == 200
    assert "词典结果" in response.text


def test_user_input_is_escaped_into_the_page(tmp_path, sample_db):
    """This surface renders HTML from Python, so escaping is this code's job.

    The query is echoed back into the search box, which is the interpolation an
    injected string would ride in on.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    jar = session_cookie(app)
    response = run(
        call(
            app,
            "POST",
            f"{WEB_MOUNT_PATH}/lookup",
            headers=edge(),
            cookies=jar,
            data=form(q='"><script>alert(1)</script>'),
        )
    )
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_only_urlencoded_form_bodies_are_accepted(tmp_path, sample_db):
    """No multipart parser is reachable from the network on this surface."""
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    jar = session_cookie(app)
    response = run(
        call(
            app,
            "POST",
            f"{WEB_MOUNT_PATH}/lookup",
            headers=edge(),
            cookies=jar,
            files={"q": ("q.txt", "共鸣者".encode("utf-8"))},
        )
    )
    assert response.status_code == 400


def test_a_dictionary_answer_is_not_labelled_as_a_model_translation(
    tmp_path, sample_db
):
    """Provenance is the one thing this label carries, so it must be right.

    The pipeline emits kinds "exact"/"fuzzy"/"llm" and directions "zh"/"en".
    A renderer that tested for a kind named "dictionary" matched none of them
    and fell through to "model" for every answer - so an official dictionary
    hit, on a product built around dictionary-first, was reported to the owner
    as something the model made up.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    jar = session_cookie(app)
    response = run(
        call(
            app,
            "POST",
            f"{WEB_MOUNT_PATH}/translate",
            headers=edge(),
            cookies=jar,
            data=form(text="共鸣者"),
        )
    )
    assert response.status_code == 200
    assert "Resonator" in response.text
    # Scoped to the result card's own heading: the page footer legitimately
    # mentions the model ("...才会调用模型。"), so a whole-page search for that
    # word would pass or fail for the wrong reason.
    heading = re.search(r'<div class="card"><h2>(.*?)</h2>', response.text)
    assert heading is not None, response.text
    label = heading.group(1)
    assert "词典" in label, label
    assert "模型" not in label, label
    # ...and the direction reads as Chinese, not as the raw pipeline token.
    assert "译为英文" in label, label
    assert label.strip() != "en", label


def test_an_error_keeps_the_form_and_what_was_typed(tmp_path, sample_db):
    """An error page the owner cannot retry from is a dead end on a phone.

    With no model configured the translation path refuses rather than echoing
    the source text back as though it had been translated - so this exercises
    a real refusal, and requires that the refusal still renders a usable form
    carrying the submitted text.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    jar = session_cookie(app)
    sentence = "今汐在云陵谷使用了风羽为刃。"
    response = run(
        call(
            app,
            "POST",
            f"{WEB_MOUNT_PATH}/translate",
            headers=edge(),
            cookies=jar,
            data=form(text=sentence),
        )
    )
    assert response.status_code == 503
    assert "翻译服务暂时不可用" in response.text
    # The form came back...
    assert "<textarea" in response.text
    # ...carrying what was typed, so the owner can edit rather than retype.
    assert sentence in response.text


def test_the_page_carries_its_hardening_headers(tmp_path, sample_db):
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


def test_the_web_layer_adds_no_cross_origin_permission(tmp_path, sample_db):
    """Criterion: same-origin architecture, no CORS relaxation.

    The browser reaches this surface on the same host name as the API through a
    second path prefix, so there is no cross-origin request to permit. Any
    access-control header appearing here would mean that stopped being true.
    """
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(
        call(
            app,
            "GET",
            f"{WEB_MOUNT_PATH}/",
            headers={**edge(), "Origin": "https://elsewhere.example"},
        )
    )
    assert not [
        name for name in response.headers if name.lower().startswith("access-control-")
    ]


def test_the_interface_is_in_chinese(tmp_path, sample_db):
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert 'lang="zh-CN"' in response.text
    for label in ("查词", "翻译", "私用工具"):
        assert label in response.text


def test_the_page_declares_a_mobile_viewport(tmp_path, sample_db):
    """Mobile-first is a rendering claim, and this is the part of it that is
    checkable without a browser: without this meta element a phone lays the
    page out at a desktop width and scales it down."""
    app, _store, _device, _token = build_web_app(tmp_path, sample_db)
    response = run(call(app, "GET", f"{WEB_MOUNT_PATH}/", headers=edge()))
    assert 'name="viewport"' in response.text
    assert "width=device-width" in response.text


# -------------------------------------------------------------- session store


def test_sessions_expire_on_a_monotonic_clock():
    store = SessionStore(ttl_seconds=100, max_sessions=8)

    class Principal:
        device_id = "abc"

    session = store.create(Principal(), now=1000.0)
    assert store.resolve(session.session_id, now=1099.0) is not None
    assert store.resolve(session.session_id, now=1100.0) is None
    # ...and the expired entry is dropped rather than merely hidden.
    assert len(store) == 0


def test_the_session_map_is_bounded():
    store = SessionStore(ttl_seconds=1000, max_sessions=3)

    class Principal:
        device_id = "abc"

    for _ in range(10):
        store.create(Principal(), now=1.0)
    assert len(store) == 3


def test_an_absent_identifier_resolves_to_nothing():
    store = SessionStore(ttl_seconds=1000, max_sessions=3)
    assert store.resolve(None) is None
    assert store.resolve("") is None
    assert store.resolve("never-issued") is None
