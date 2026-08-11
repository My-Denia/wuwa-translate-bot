"""HTTP adapter tests: contract, credentials, limits and budgets.

Everything runs in-process against the ASGI app (no socket, no uvicorn), in the
same duck-typed style as the bot tests.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from wuwaterm_api.app import create_app
from wuwaterm_api.auth import (
    MIN_SECRET_LENGTH,
    SCOPE_META,
    SCOPE_TRANSLATE,
    TOKEN_SCHEME,
    DeviceStore,
    DeviceStoreError,
    parse_token,
)
from wuwaterm_api.errors import STATUS_BY_CODE
from wuwaterm_api.settings import ApiConfigError, ApiSettings

ROOT = Path(__file__).resolve().parents[1]


def build_settings(tmp_path: Path, db_path: Path, **overrides) -> ApiSettings:
    defaults = dict(
        db_path=db_path,
        device_db_path=tmp_path / "api-state" / "devices.db",
        rate_limit_per_minute=100,
        llm_calls_per_minute=100,
        llm_max_concurrency=2,
        max_body_bytes=2048,
        request_timeout_seconds=30.0,
    )
    defaults.update(overrides)
    return ApiSettings(**defaults)


def build_client_app(tmp_path: Path, db_path: Path, **overrides):
    settings = build_settings(tmp_path, db_path, **overrides)
    store = DeviceStore(settings.device_db_path)
    store.initialize()
    app = create_app(settings, device_store=store)
    return app, store



# The service never mints a secret; the operator supplies one. Tests do the
# same, so what they exercise is the real registration path.
TEST_SECRET_BASE = "unguessable-material-for-tests-0123456789abcdef"


def issue_device(store, name: str, scopes=None, secret: str | None = None):
    material = (
        secret
        if secret is not None
        else f"{TEST_SECRET_BASE}-{name.replace(' ', '-')}"
    )
    device = store.issue(name, scopes, secret=material)
    return device, f"{TOKEN_SCHEME}.{device.device_id}.{material}"


def run(coro):
    return asyncio.run(coro)


async def call(app, method: str, url: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        return await client.request(method, url, **kwargs)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def enable_mock_llm(monkeypatch, calls, response_factory) -> None:
    monkeypatch.setenv("WUWATERM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("WUWATERM_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("WUWATERM_OPENAI_MODEL", "test-model")

    async def fake_call(
        locked_text,
        locks,
        html_mode=False,
        to_chinese=False,
        timeout_seconds=30.0,
        transport=None,
    ):
        calls.append((locked_text, locks))
        return await response_factory(locked_text, locks)

    monkeypatch.setattr("wuwaterm.sentence._call_llm_async", fake_call)


def disable_llm(monkeypatch) -> None:
    for name in (
        "WUWATERM_OPENAI_BASE_URL",
        "WUWATERM_OPENAI_API_KEY",
        "WUWATERM_OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


def test_healthz_needs_no_credential(tmp_path, sample_db):
    app, _ = build_client_app(tmp_path, sample_db)

    response = run(call(app, "GET", "/healthz"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_dictionary_readability(tmp_path, sample_db):
    app, _ = build_client_app(tmp_path, sample_db)
    broken_app, _ = build_client_app(
        tmp_path / "broken", tmp_path / "missing" / "terms.db"
    )

    ok = run(call(app, "GET", "/readyz"))
    bad = run(call(broken_app, "GET", "/readyz"))

    assert ok.status_code == 200
    assert ok.json()["status"] == "ready"
    assert bad.status_code == 503
    assert bad.json()["error"]["code"] == "internal"


# --------------------------------------------------------------------------
# Device credentials
# --------------------------------------------------------------------------


def test_translation_requires_a_credential(tmp_path, sample_db):
    app, _ = build_client_app(tmp_path, sample_db)

    response = run(call(app, "POST", "/v1/translations", json={"text": "声骸"}))

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert set(body) == {"error", "request_id"}


@pytest.mark.parametrize(
    "header",
    [
        {"Authorization": "Bearer not-a-token"},
        {"Authorization": "Bearer wtd1.deadbeef.wrongsecret"},
        {"Authorization": "Token wtd1.a.b"},
        {"Authorization": ""},
    ],
)
def test_bad_credentials_are_indistinguishable(tmp_path, sample_db, header):
    app, store = build_client_app(tmp_path, sample_db)
    issue_device(store, "owner desktop")

    response = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=header)
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_revoked_device_is_rejected(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    device, token = issue_device(store, "owner desktop")

    before = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token))
    )
    store.revoke(device.device_id)
    after = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token))
    )

    assert before.status_code == 200
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "unauthorized"


def test_scope_is_enforced(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    _, meta_only = issue_device(store, "reader", [SCOPE_META])
    _, translate_only = issue_device(store, "translator", [SCOPE_TRANSLATE])

    forbidden = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "声骸"},
            headers=bearer(meta_only),
        )
    )
    also_forbidden = run(call(app, "GET", "/v1/meta", headers=bearer(translate_only)))
    allowed = run(call(app, "GET", "/v1/meta", headers=bearer(meta_only)))

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"
    assert also_forbidden.status_code == 403
    assert allowed.status_code == 200


def test_only_a_hash_of_the_secret_is_stored(tmp_path):
    store = DeviceStore(tmp_path / "devices.db")
    device, token = issue_device(store, "owner desktop")
    parsed = parse_token(token)

    assert parsed is not None
    assert parsed[0] == device.device_id
    raw_secret = parsed[1]
    on_disk = (tmp_path / "devices.db").read_bytes()
    assert raw_secret.encode("utf-8") not in on_disk
    assert token.encode("utf-8") not in on_disk
    # listing never reveals credential material
    listed = store.list_devices()
    assert [item.device_id for item in listed] == [device.device_id]
    assert raw_secret not in json.dumps([item.__dict__ for item in listed], default=str)


def test_the_store_never_returns_credential_material(tmp_path):
    """issue() hands back a Device and nothing else, by construction."""
    store = DeviceStore(tmp_path / "devices.db")

    result = store.issue("owner desktop", None, secret="x" * MIN_SECRET_LENGTH)

    assert not isinstance(result, tuple)
    assert "x" * MIN_SECRET_LENGTH not in json.dumps(result.__dict__, default=str)


def test_a_weak_supplied_secret_is_refused(tmp_path):
    store = DeviceStore(tmp_path / "devices.db")

    with pytest.raises(DeviceStoreError):
        store.issue("owner desktop", None, secret="short")
    with pytest.raises(DeviceStoreError):
        store.issue("owner desktop", None, secret="   ")
    assert store.list_devices() == []


def test_unknown_scope_is_refused(tmp_path):
    store = DeviceStore(tmp_path / "devices.db")

    with pytest.raises(DeviceStoreError):
        issue_device(store, "owner desktop", ["translate", "root"])


def test_revoking_an_unknown_device_fails(tmp_path):
    store = DeviceStore(tmp_path / "devices.db")

    with pytest.raises(DeviceStoreError):
        store.revoke("does-not-exist")


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------


def test_rate_limit_returns_the_stable_code(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db, rate_limit_per_minute=2)
    _, token = issue_device(store, "owner desktop")

    statuses = [
        run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers=bearer(token),
            )
        )
        for _ in range(3)
    ]

    assert [item.status_code for item in statuses] == [200, 200, 429]
    assert statuses[-1].json()["error"]["code"] == "rate_limited"


def test_rate_limit_is_per_device(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db, rate_limit_per_minute=1)
    _, first = issue_device(store, "device one")
    _, second = issue_device(store, "device two")

    run(call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(first)))
    blocked = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(first))
    )
    other = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(second))
    )

    assert blocked.status_code == 429
    assert other.status_code == 200


def test_oversized_body_is_refused_before_parsing(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db, max_body_bytes=256)
    _, token = issue_device(store, "owner desktop")

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "x" * 4000},
            headers=bearer(token),
        )
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_input_over_translation_limit_returns_input_too_long(
    monkeypatch, tmp_path, sample_db
):
    app, store = build_client_app(tmp_path, sample_db, max_body_bytes=64 * 1024)
    _, token = issue_device(store, "owner desktop")
    disable_llm(monkeypatch)

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "中" * 2100},
            headers=bearer(token),
        )
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "input_too_long"


def test_request_timeout_returns_the_envelope(monkeypatch, tmp_path, sample_db):
    app, store = build_client_app(
        tmp_path, sample_db, request_timeout_seconds=0.05, llm_max_concurrency=1
    )
    _, token = issue_device(store, "owner desktop")
    calls: list[tuple[str, object]] = []

    async def slow(locked_text, locks):
        await asyncio.sleep(5)
        return locked_text

    enable_mock_llm(monkeypatch, calls, slow)

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "需要翻译的一个句子"},
            headers=bearer(token),
        )
    )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "internal"


def test_invalid_payload_returns_invalid_request(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")

    missing_text = run(
        call(app, "POST", "/v1/translations", json={}, headers=bearer(token))
    )
    bad_direction = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "声骸", "to": "fr"},
            headers=bearer(token),
        )
    )

    assert missing_text.status_code == 400
    assert missing_text.json()["error"]["code"] == "invalid_request"
    assert bad_direction.status_code == 400
    assert bad_direction.json()["error"]["code"] == "invalid_request"
    # the rejected text is never echoed back
    assert "声骸" not in bad_direction.text


# --------------------------------------------------------------------------
# Translation paths
# --------------------------------------------------------------------------


def test_exact_dictionary_hit_uses_no_model_call(monkeypatch, tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")
    calls: list[tuple[str, object]] = []

    async def unused(locked_text, locks):  # pragma: no cover - must not run
        raise AssertionError("dictionary hit must not call the model")

    enable_mock_llm(monkeypatch, calls, unused)

    response = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token))
    )

    body = response.json()
    assert response.status_code == 200
    assert body["kind"] == "exact"
    assert body["text"] == "Echo"
    assert body["direction"] == "en"
    assert body["dictionary_miss"] is False
    assert body["request_id"]
    assert not calls


def test_forced_direction_is_honored(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "Echo", "to": "zh"},
            headers=bearer(token),
        )
    )

    body = response.json()
    assert body["direction"] == "zh"
    assert body["text"] == "声骸"


def test_fuzzy_hit_is_reported_as_fuzzy(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "shenghai"},
            headers=bearer(token),
        )
    )

    assert response.json()["kind"] == "fuzzy"


def test_model_path_reports_kind_and_dictionary_miss(monkeypatch, tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")
    calls: list[tuple[str, object]] = []

    async def answer(locked_text, locks):
        return "widget"

    enable_mock_llm(monkeypatch, calls, answer)

    response = run(
        call(
            app, "POST", "/v1/translations", json={"text": "foobar"}, headers=bearer(token)
        )
    )

    body = response.json()
    assert body["kind"] == "llm"
    assert body["text"] == "widget"
    assert body["dictionary_miss"] is True
    assert len(calls) == 1


def test_model_failure_maps_to_llm_unavailable(monkeypatch, tmp_path, sample_db):
    from wuwaterm.sentence import (
        TRANSLATION_UNAVAILABLE_NOTICE,
        LLMTranslationError,
    )

    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")
    calls: list[tuple[str, object]] = []

    async def boom(locked_text, locks):
        raise LLMTranslationError(TRANSLATION_UNAVAILABLE_NOTICE, reason="upstream")

    enable_mock_llm(monkeypatch, calls, boom)

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "需要翻译的一个句子"},
            headers=bearer(token),
        )
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "llm_unavailable"
    # the Telegram-worded notice never reaches the HTTP contract
    assert TRANSLATION_UNAVAILABLE_NOTICE not in response.text


def test_request_id_is_always_server_generated(tmp_path, sample_db, caplog):
    """The correlation id is minted server-side; an inbound X-Request-Id is
    ignored, never echoed and never logged.

    Trusting the header let a caller put its own token (which fits the shape
    wtd1.<id>.<secret>) into the id, which was then written to the auth-reject
    log and the error envelope. AC16 forbids a raw credential reaching logs or
    telemetry, so nothing the caller sends may become the id.
    """
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")

    generated = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token))
    )
    # Shaped exactly like a device token, and matched the old accept charset.
    token_shaped = "wtd1.deadbeef.%s" % ("s" * 40)
    with caplog.at_level("INFO", logger="wuwaterm_api"):
        supplied = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers={**bearer(token), "X-Request-Id": token_shaped},
            )
        )

    # The server's generated id is what is returned and echoed in the header.
    assert generated.headers["X-Request-Id"] == generated.json()["request_id"]
    assert supplied.headers["X-Request-Id"] == supplied.json()["request_id"]
    # The client's value did not become the id, on the wire or in a log.
    assert supplied.json()["request_id"] != token_shaped
    assert token_shaped not in supplied.text
    assert token_shaped not in caplog.text


# --------------------------------------------------------------------------
# Dictionary and metadata
# --------------------------------------------------------------------------


def test_terms_returns_official_strings(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")

    hit = run(call(app, "GET", "/v1/terms", params={"q": "声骸"}, headers=bearer(token)))
    miss = run(
        call(app, "GET", "/v1/terms", params={"q": "not-a-term"}, headers=bearer(token))
    )
    empty = run(call(app, "GET", "/v1/terms", params={"q": "  "}, headers=bearer(token)))

    assert hit.status_code == 200
    assert hit.json()["matches"][0]["en"] == "Echo"
    assert miss.json()["matches"] == []
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "invalid_request"


def test_meta_exposes_provenance_without_paths_or_secrets(
    monkeypatch, tmp_path, sample_db
):
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")
    disable_llm(monkeypatch)

    response = run(call(app, "GET", "/v1/meta", headers=bearer(token)))

    body = response.json()
    assert body["term_count"] > 0
    assert body["source_profile"] == "dimbreath_legacy"
    assert body["api_version"] == "v1"
    assert body["llm_configured"] is False
    text = response.text
    assert str(sample_db) not in text
    assert "terms.db" not in text
    assert "devices.db" not in text
    assert token not in text


# --------------------------------------------------------------------------
# Budgets (AC7): per-process concurrency cap and per-minute call cap
# --------------------------------------------------------------------------


def test_model_concurrency_never_exceeds_the_configured_cap(
    monkeypatch, tmp_path, sample_db
):
    cap = 2
    app, store = build_client_app(
        tmp_path,
        sample_db,
        llm_max_concurrency=cap,
        rate_limit_per_minute=100,
        llm_calls_per_minute=100,
        request_timeout_seconds=30.0,
        # This test measures the MODEL concurrency cap, which is a later gate
        # than credential admission. Give it enough auth slots to admit its
        # whole burst, so the non-queuing auth admission does not shed requests
        # before they reach the model cap being measured here.
        auth_max_concurrency=8,
    )
    _, token = issue_device(store, "owner desktop")
    calls: list[tuple[str, object]] = []
    state = {"in_flight": 0, "peak": 0}

    async def slow(locked_text, locks):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.05)
            return "done"
        finally:
            state["in_flight"] -= 1

    enable_mock_llm(monkeypatch, calls, slow)

    async def hammer():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://api.test", timeout=30.0
        ) as client:
            return await asyncio.gather(
                *[
                    client.post(
                        "/v1/translations",
                        json={"text": f"需要翻译的第{index}个句子"},
                        headers=bearer(token),
                    )
                    for index in range(8)
                ]
            )

    responses = run(hammer())

    assert [item.status_code for item in responses] == [200] * 8
    assert len(calls) == 8
    assert state["peak"] == cap, state["peak"]


def test_per_minute_model_budget_overflow_returns_the_stable_code(
    monkeypatch, tmp_path, sample_db
):
    budget = 3
    app, store = build_client_app(
        tmp_path,
        sample_db,
        llm_calls_per_minute=budget,
        rate_limit_per_minute=100,
    )
    _, token = issue_device(store, "owner desktop")
    calls: list[tuple[str, object]] = []

    async def answer(locked_text, locks):
        return "done"

    enable_mock_llm(monkeypatch, calls, answer)

    codes = []
    for index in range(budget + 2):
        response = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": f"需要翻译的第{index}个句子"},
                headers=bearer(token),
            )
        )
        codes.append(response.status_code)

    assert codes[:budget] == [200] * budget
    assert codes[budget:] == [503, 503]
    assert len(calls) == budget
    overflow = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "另一个需要翻译的句子"},
            headers=bearer(token),
        )
    )
    assert overflow.json()["error"]["code"] == "llm_budget_exhausted"


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_error_codes_and_statuses_are_pinned():
    assert STATUS_BY_CODE == {
        "unauthorized": 401,
        "forbidden": 403,
        "rate_limited": 429,
        "payload_too_large": 413,
        "invalid_request": 400,
        "input_too_long": 422,
        "llm_unavailable": 503,
        "llm_budget_exhausted": 503,
        "internal": 500,
    }


def test_committed_openapi_snapshot_matches_the_application():
    result = subprocess.run(
        [sys.executable, "scripts/check_api_contract.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_openapi_snapshot_documents_the_versioned_surface():
    from scripts.check_api_contract import check_tokens

    raw = (ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8")
    document = json.loads(raw)

    assert sorted(document["paths"]) == [
        "/healthz",
        "/readyz",
        "/v1/meta",
        "/v1/terms",
        "/v1/translations",
    ]
    # The one committed JSON artifact must also respect the product token bans;
    # scripts/check_non_goals.py does not read .json files, so the same
    # patterns are re-applied here through the contract gate.
    assert check_tokens(raw) == []


def test_settings_reject_out_of_range_values(monkeypatch):
    monkeypatch.setenv("WUWATERM_API_PORT", "70000")

    with pytest.raises(ApiConfigError):
        ApiSettings.from_env()


def test_settings_defaults_bind_to_loopback(monkeypatch):
    for name in (
        "WUWATERM_API_BIND",
        "WUWATERM_API_PORT",
        "WUWATERM_API_LLM_MAX_CONCURRENCY",
        "WUWATERM_API_LLM_CALLS_PER_MINUTE",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = ApiSettings.from_env()

    assert settings.bind == "127.0.0.1"
    assert settings.port == 8787
    # Deliberately smaller than the bot's budget; the documented worst case is
    # the SUM of the per-process budgets, never a shared global one.
    assert settings.llm_max_concurrency == 2
    assert settings.llm_calls_per_minute == 30


# --------------------------------------------------------------------------
# Review follow-ups: streaming cap, routing shape, credential-store writes,
# refusing a pretend model answer, and contract completeness
# --------------------------------------------------------------------------


def test_body_cap_holds_without_a_content_length_header(tmp_path, sample_db):
    """A chunked caller must not be able to make the process buffer freely."""
    app, store = build_client_app(tmp_path, sample_db, max_body_bytes=256)
    _, token = issue_device(store, "owner desktop")

    async def chunked():
        yield b'{"text": "'
        for _ in range(50):
            yield b"x" * 64
        yield b'"}'

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            content=chunked(),
            headers={**bearer(token), "Content-Type": "application/json"},
        )
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_negative_content_length_is_an_invalid_request(tmp_path, sample_db):
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            content=b'{"text": "hi"}',
            headers={
                **bearer(token),
                "Content-Type": "application/json",
                "Content-Length": "-1",
            },
        )
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/v2/meta", 404),
        ("GET", "/nope", 404),
        ("DELETE", "/healthz", 405),
    ],
)
def test_routing_failures_use_the_stable_envelope(
    tmp_path, sample_db, method, path, status
):
    app, _ = build_client_app(tmp_path, sample_db)

    response = run(call(app, method, path))

    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error", "request_id"}
    assert body["error"]["code"] == "invalid_request"
    assert "detail" not in body


def test_fresh_credential_store_answers_401_not_an_error(tmp_path, sample_db):
    """A first install has no devices.db; that is a rejection, not a crash."""
    settings = build_settings(tmp_path, sample_db)
    app = create_app(settings, device_store=DeviceStore(settings.device_db_path))
    assert not settings.device_db_path.exists()

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "声骸"},
            headers=bearer("wtd1.deadbeef.secret"),
        )
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_rate_limited_requests_do_not_write_to_the_credential_store(
    tmp_path, sample_db
):
    app, store = build_client_app(tmp_path, sample_db, rate_limit_per_minute=1)
    device, token = issue_device(store, "owner desktop")

    run(call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token)))
    admitted = store.list_devices()[0].last_used_at
    assert admitted is not None

    for _ in range(5):
        blocked = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers=bearer(token),
            )
        )
        assert blocked.status_code == 429

    assert store.list_devices()[0].last_used_at == admitted


def test_unauthenticated_requests_never_touch_the_credential_store(
    tmp_path, sample_db
):
    app, store = build_client_app(tmp_path, sample_db)
    device, _token = issue_device(store, "owner desktop")

    run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "声骸"},
            headers=bearer("wtd1.%s.wrong" % device.device_id),
        )
    )

    assert store.list_devices()[0].last_used_at is None


def test_model_path_is_refused_when_no_model_is_configured(
    monkeypatch, tmp_path, sample_db
):
    """Without a model the pipeline returns source text; HTTP must not lie."""
    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")
    disable_llm(monkeypatch)

    refused = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "需要翻译的一个句子"},
            headers=bearer(token),
        )
    )
    # A dictionary hit still answers, because it needs no model at all.
    dictionary = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token))
    )

    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "llm_unavailable"
    assert dictionary.status_code == 200
    assert dictionary.json()["kind"] == "exact"


def test_openapi_declares_the_credential_scheme_and_every_failure_shape():
    document = json.loads(
        (ROOT / "docs" / "api" / "openapi.json").read_text(encoding="utf-8")
    )

    schemes = document["components"]["securitySchemes"]
    assert any(
        item.get("type") == "http" and item.get("scheme") == "bearer"
        for item in schemes.values()
    ), schemes
    translations = document["paths"]["/v1/translations"]["post"]
    assert translations["security"]
    assert "504" in translations["responses"]
    code = document["components"]["schemas"]["ErrorDetailBody"]["properties"]["code"]
    assert set(code["enum"]) == set(STATUS_BY_CODE)
    assert document["paths"]["/v1/terms"]["get"]["parameters"][0]["required"] is True


def test_a_slow_body_is_bounded_by_the_request_time_budget(tmp_path, sample_db):
    """The time budget wraps the body read, not the other way round."""
    app, store = build_client_app(
        tmp_path, sample_db, request_timeout_seconds=0.2, max_body_bytes=4096
    )
    _, token = issue_device(store, "owner desktop")

    async def drip():
        yield b'{"text": "'
        for _ in range(20):
            await asyncio.sleep(0.1)
            yield b"x"
        yield b'"}'

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            content=drip(),
            headers={**bearer(token), "Content-Type": "application/json"},
        )
    )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "internal"


def test_stored_verifier_is_salted_and_slow_to_search(tmp_path):
    """Operator-chosen secrets need a KDF, not a bare digest."""
    import hashlib

    from wuwaterm_api import auth as auth_module

    store = DeviceStore(tmp_path / "devices.db")
    secret = "the-same-secret-for-both-devices-0123456789"
    first = store.issue("device one", None, secret=secret)
    second = store.issue("device two", None, secret=secret)

    rows = {}
    import sqlite3

    with sqlite3.connect(tmp_path / "devices.db") as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT device_id, salt, token_hash FROM devices"):
            rows[row["device_id"]] = (bytes(row["salt"]), bytes(row["token_hash"]))

    # Same secret, different salt, therefore different stored verifiers.
    assert rows[first.device_id][0] != rows[second.device_id][0]
    assert rows[first.device_id][1] != rows[second.device_id][1]
    # And the stored value is not a bare digest of the secret.
    assert hashlib.sha256(secret.encode()).digest() not in {
        value for _salt, value in rows.values()
    }
    assert auth_module.SCRYPT_N >= 1 << 14


def test_a_secret_containing_the_token_separator_round_trips(tmp_path, sample_db):
    """The credential charset is unconstrained; dots must not break auth."""
    app, store = build_client_app(tmp_path, sample_db)
    dotted = "v1.abc.def-0123456789abcdefghijklmnop=="
    assert "." in dotted and len(dotted) >= MIN_SECRET_LENGTH
    _, token = issue_device(store, "owner desktop", secret=dotted)

    parsed = parse_token(token)
    response = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token))
    )

    assert parsed == (token.split(".", 2)[1], dotted)
    assert response.status_code == 200


def test_a_secret_with_surrounding_whitespace_is_refused(tmp_path):
    """Refuse rather than trim: a trimmed secret would never authenticate."""
    store = DeviceStore(tmp_path / "devices.db")
    body = "x" * MIN_SECRET_LENGTH

    for candidate in (f" {body}", f"{body} ", f"\t{body}\n"):
        with pytest.raises(DeviceStoreError):
            store.issue("owner desktop", None, secret=candidate)
    assert store.list_devices() == []


def test_a_store_written_by_an_older_shape_is_reported_clearly(tmp_path):
    import sqlite3

    path = tmp_path / "devices.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE devices (device_id TEXT PRIMARY KEY, device_name TEXT,"
            " scopes TEXT, created_at TEXT, revoked_at TEXT, last_used_at TEXT)"
        )

    with pytest.raises(DeviceStoreError) as excinfo:
        DeviceStore(path).initialize()

    assert "older device store" in str(excinfo.value)


def test_a_store_left_at_the_old_default_path_is_not_silently_abandoned(tmp_path):
    """The store moved out of the bot's writable mount between builds.

    An installation that ran on the old default keeps every verifier in
    state/api/devices.db. Creating an empty store at the new path would look
    like a clean start while every registered device stopped authenticating,
    so the move has to be refused loudly instead.
    """
    legacy = tmp_path / "state" / "api" / "devices.db"
    legacy.parent.mkdir(parents=True)
    DeviceStore(legacy).initialize()
    assert legacy.exists()

    current = tmp_path / "state-api" / "devices.db"
    with pytest.raises(DeviceStoreError) as excinfo:
        DeviceStore(current).initialize()

    message = str(excinfo.value)
    assert str(legacy) in message
    assert str(current) in message
    assert not current.exists()


def test_a_store_at_both_paths_is_refused_rather_than_guessed(tmp_path):
    """An earlier start may already have created an empty store here."""
    legacy = tmp_path / "state" / "api" / "devices.db"
    legacy.parent.mkdir(parents=True)
    DeviceStore(legacy).initialize()

    current = tmp_path / "state-api" / "devices.db"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"SQLite format 3\x00")

    with pytest.raises(DeviceStoreError) as excinfo:
        DeviceStore(current).initialize()

    assert str(legacy) in str(excinfo.value)


def test_an_explicit_store_path_never_looks_for_the_old_default(tmp_path):
    """Only the default layout ever had the old path."""
    legacy = tmp_path / "state" / "api" / "devices.db"
    legacy.parent.mkdir(parents=True)
    DeviceStore(legacy).initialize()

    chosen = tmp_path / "somewhere-else" / "devices.db"
    DeviceStore(chosen).initialize()

    assert chosen.exists()


def test_a_named_store_is_used_even_when_it_sits_in_a_state_api_directory(tmp_path):
    """The exemption is about being NAMED, not about the directory's name.

    An operator who sets WUWATERM_API_DEVICE_DB_PATH means that store, and the
    obvious value to set is a path under state-api/. Deciding by the parent
    directory's name would make the escape hatch unusable in exactly the case
    the message suggests it for.
    """
    legacy = tmp_path / "state" / "api" / "devices.db"
    legacy.parent.mkdir(parents=True)
    DeviceStore(legacy).initialize()

    named = tmp_path / "state-api" / "devices.db"
    DeviceStore(named, guard_legacy_default=False).initialize()

    assert named.exists()


def test_settings_report_whether_the_store_path_was_chosen(tmp_path, monkeypatch):
    monkeypatch.delenv("WUWATERM_API_DEVICE_DB_PATH", raising=False)
    monkeypatch.setenv("WUWATERM_API_STATE_DIR", str(tmp_path / "state-api"))
    assert ApiSettings.from_env().device_db_is_default is True

    monkeypatch.setenv(
        "WUWATERM_API_DEVICE_DB_PATH", str(tmp_path / "state-api" / "devices.db")
    )
    chosen = ApiSettings.from_env()
    assert chosen.device_db_is_default is False
    assert chosen.device_db_path == tmp_path / "state-api" / "devices.db"


def test_the_moved_store_starts_normally_once_it_is_in_place(tmp_path):
    """The guard must not fire when there is nothing left behind."""
    current = tmp_path / "state-api" / "devices.db"
    DeviceStore(current).initialize()

    assert current.exists()


def test_credential_verification_is_bounded_before_any_device_limit(
    tmp_path, sample_db
):
    """The deliberately expensive check must not become the load itself."""
    app, store = build_client_app(tmp_path, sample_db, auth_max_concurrency=1)
    issue_device(store, "owner desktop")
    state = {"in_flight": 0, "peak": 0}
    real = DeviceStore.authenticate

    def watched(self, token):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            return real(self, token)
        finally:
            state["in_flight"] -= 1

    DeviceStore.authenticate = watched
    try:

        async def hammer():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://api.test", timeout=30.0
            ) as client:
                return await asyncio.gather(
                    *[
                        client.post(
                            "/v1/translations",
                            json={"text": "声骸"},
                            headers=bearer("wtd1.deadbeef.%s" % ("x" * 40)),
                        )
                        for _ in range(6)
                    ]
                )

        responses = run(hammer())
    finally:
        DeviceStore.authenticate = real

    # Non-queuing admission: an admitted attempt is rejected as a bad
    # credential (401); an attempt that found the verifier full is shed (429).
    # Either way the expensive check itself never runs more than the bound.
    # `401 in` guards against the vacuous all-429 case (verification never ran).
    statuses = {item.status_code for item in responses}
    assert 401 in statuses and statuses <= {401, 429}, statuses
    assert state["peak"] == 1, state["peak"]


def test_an_unusable_store_is_one_more_uniform_rejection(tmp_path, sample_db):
    """Loud at startup, uniform on the request path."""
    import sqlite3

    settings = build_settings(tmp_path, sample_db)
    settings.device_db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.device_db_path) as conn:
        conn.execute("CREATE TABLE devices (device_id TEXT PRIMARY KEY)")
    app = create_app(settings, device_store=DeviceStore(settings.device_db_path))

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "声骸"},
            headers=bearer("wtd1.deadbeef.%s" % ("x" * 40)),
        )
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    # The operator still gets the real reason where it has an audience.
    with pytest.raises(DeviceStoreError):
        DeviceStore(settings.device_db_path).initialize()


def test_a_secret_that_cannot_be_sent_in_a_header_is_refused(tmp_path):
    """Registering an unusable credential is the worst kind of failure."""
    store = DeviceStore(tmp_path / "devices.db")
    body = "x" * MIN_SECRET_LENGTH

    for candidate in (
        f"{body} with space",
        f"{body}\nnewline",
        f"{body}\ttab",
        f"{body}\x7fdel",
        f"{body}密码",
    ):
        with pytest.raises(DeviceStoreError):
            store.issue("owner desktop", None, secret=candidate)
    assert store.list_devices() == []


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX permission bits; the deployment target is Linux and CI checks it",
)
def test_every_file_carrying_verifier_material_is_restricted(tmp_path):
    """The write-ahead log holds the same rows as the database."""
    import stat

    store = DeviceStore(tmp_path / "state" / "devices.db")
    issue_device(store, "owner desktop")
    store.authenticate("wtd1.nope.%s" % ("x" * 40))

    paths = [store.path.parent, store.path]
    paths += [
        store.path.with_name(store.path.name + suffix) for suffix in ("-wal", "-shm")
    ]
    for path in paths:
        if not path.exists():
            continue
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert not mode & 0o077, (path, oct(mode))


def test_a_corrupt_store_is_still_a_uniform_rejection(tmp_path, sample_db):
    """No seam may answer differently from every other rejection."""
    settings = build_settings(tmp_path, sample_db)
    settings.device_db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.device_db_path.write_bytes(b"this is not a database at all")
    app = create_app(settings, device_store=DeviceStore(settings.device_db_path))

    response = run(
        call(
            app,
            "POST",
            "/v1/translations",
            json={"text": "声骸"},
            headers=bearer("wtd1.deadbeef.%s" % ("x" * 40)),
        )
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_a_cancelled_request_does_not_leak_a_verification_slot(tmp_path, sample_db):
    """The bound is held inside the worker thread, so it cannot be stranded."""
    app, store = build_client_app(
        tmp_path, sample_db, auth_max_concurrency=1, request_timeout_seconds=1.0
    )
    _, token = issue_device(store, "owner desktop")
    real = DeviceStore.authenticate

    def slow(self, presented):
        time.sleep(3.0)
        return real(self, presented)

    DeviceStore.authenticate = slow
    try:
        timed_out = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers=bearer(token),
            )
        )
    finally:
        DeviceStore.authenticate = real

    assert timed_out.status_code == 504
    # The worker always runs to completion, so once it finishes the slot is
    # free again. Without the worker-thread bound it would stay stranded and
    # every later request would time out waiting for it.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if app.state.auth_slots.acquire(blocking=False):
            app.state.auth_slots.release()
            break
        time.sleep(0.05)
    else:  # pragma: no cover - only on a real leak
        raise AssertionError("verification slot was never released")

    recovered = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token))
    )
    assert recovered.status_code == 200


def test_admission_slot_is_released_if_verification_is_cancelled_before_it_runs(
    tmp_path, sample_db, monkeypatch
):
    """The admission slot must be released on the loop, not only in the worker.

    If the to_thread verification job is cancelled while still QUEUED on a
    saturated executor, the worker never runs — so releasing only inside the
    worker would leak the slot permanently and wedge every later request into
    429. Here `to_thread` is cancelled before the worker callable runs, exactly
    that case, and the slot must still be reacquirable afterwards.
    """
    from fastapi.security import HTTPAuthorizationCredentials

    import wuwaterm_api.app as appmod

    app, store = build_client_app(tmp_path, sample_db, auth_max_concurrency=1)
    _, token = issue_device(store, "owner desktop")

    class _Url:
        path = "/v1/translations"

    class _State:
        pass

    class _Req:
        def __init__(self, app):
            self.app = app
            self.url = _Url()
            self.state = _State()

    async def cancel_before_worker(func, *args, **kwargs):
        # The awaiting task is cancelled before the queued worker starts.
        raise asyncio.CancelledError()

    monkeypatch.setattr(appmod.asyncio, "to_thread", cancel_before_worker)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await appmod.authenticated_device(_Req(app), creds)

    run(scenario())

    # The slot survived the cancelled-before-it-ran verification.
    assert app.state.auth_slots.acquire(blocking=False)
    app.state.auth_slots.release()


# --------------------------------------------------------------------------
# Hardening fixes (gpt5.6 safety critic): loopback bind, auth admission,
# revocation TOCTOU
# --------------------------------------------------------------------------


def test_validate_loopback_bind_accepts_only_numeric_loopback():
    """A numeric loopback literal is required; everything else is refused.

    The returned value is the NORMALIZED literal that actually binds — brackets
    and surrounding whitespace are stripped — so an accepted value can never be
    one uvicorn then rejects at bind time.
    """
    from wuwaterm_api.settings import validate_loopback_bind

    assert validate_loopback_bind("127.0.0.1") == "127.0.0.1"
    assert validate_loopback_bind("127.255.255.254") == "127.255.255.254"
    assert validate_loopback_bind("::1") == "::1"
    assert validate_loopback_bind("[::1]") == "::1"
    assert validate_loopback_bind(" 127.0.0.1 ") == "127.0.0.1"
    for bad in (
        "0.0.0.0",
        "::",
        "192.168.0.10",
        "10.0.0.1",
        "8.8.8.8",
        "localhost",
        "example.com",
        "",
        "not-an-address",
    ):
        with pytest.raises(ApiConfigError):
            validate_loopback_bind(bad)


def test_from_env_rejects_a_non_loopback_bind(monkeypatch):
    """The environment override cannot expose the surface on a public interface."""
    monkeypatch.setenv("WUWATERM_API_BIND", "0.0.0.0")

    with pytest.raises(ApiConfigError):
        ApiSettings.from_env()


def test_serve_refuses_a_non_loopback_host_override(monkeypatch):
    """The --host override goes through the same guard, before anything binds."""
    import wuwaterm_api.cli as cli

    called = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: called.setdefault("kw", kw))

    code = cli.main(["serve", "--host", "0.0.0.0"])

    assert code == 2
    assert "kw" not in called  # never reached uvicorn.run


def test_serve_binds_the_validated_loopback_host(monkeypatch):
    """The happy path binds the validated loopback bind."""
    import types

    import wuwaterm_api.cli as cli

    captured = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: captured.update(kw))
    monkeypatch.setattr(
        cli, "DeviceStore", lambda *a, **k: types.SimpleNamespace(initialize=lambda: None)
    )
    monkeypatch.setattr("wuwaterm_api.app.create_app", lambda *a, **k: object())
    for name in ("WUWATERM_API_BIND", "WUWATERM_API_PORT"):
        monkeypatch.delenv(name, raising=False)

    assert cli.main(["serve"]) == 0
    assert captured["host"] == "127.0.0.1"


def test_auth_admission_sheds_load_when_the_verifier_is_full(tmp_path, sample_db):
    """When the bounded verification executor is full, an extra credentialed
    request is shed immediately (429), not queued behind an expensive scrypt.

    With the whole executor occupied, the old code blocked the worker thread on
    the semaphore and the request only ended at the time budget (504); the fix
    admits without queuing and returns the back-off code straight away.
    """
    app, store = build_client_app(
        tmp_path, sample_db, auth_max_concurrency=1, request_timeout_seconds=2.0
    )
    _, token = issue_device(store, "owner desktop")

    # Occupy the single verification slot so the executor is full.
    assert app.state.auth_slots.acquire(blocking=False)
    try:
        shed = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers=bearer(token),
            )
        )
    finally:
        app.state.auth_slots.release()

    assert shed.status_code == 429
    assert shed.json()["error"]["code"] == "rate_limited"

    # With the slot free again the same credential is admitted normally.
    admitted = run(
        call(app, "POST", "/v1/translations", json={"text": "声骸"}, headers=bearer(token))
    )
    assert admitted.status_code == 200


def test_record_use_reports_whether_a_live_row_was_stamped(tmp_path):
    """record_use returns affected rows and is_active reflects revocation."""
    store = DeviceStore(tmp_path / "devices.db")
    device, _ = issue_device(store, "owner desktop")

    assert store.record_use(device.device_id) == 1
    assert store.is_active(device.device_id) is True

    store.revoke(device.device_id)

    assert store.record_use(device.device_id) == 0
    assert store.is_active(device.device_id) is False
    assert store.is_active("does-not-exist") is False


def test_a_device_revoked_during_verification_is_refused(tmp_path, sample_db):
    """TOCTOU: a revocation that commits between the verify snapshot and
    admission must not be served. record_use stamps zero rows, so admission
    fails with 401 rather than serving the withdrawn credential."""
    app, store = build_client_app(tmp_path, sample_db)
    device, token = issue_device(store, "owner desktop")
    real = DeviceStore.authenticate

    def revoke_then_return(self, presented):
        result = real(self, presented)
        if result is not None:
            # The operator revokes in the same instant the snapshot is taken.
            self.revoke(result.device_id)
        return result

    DeviceStore.authenticate = revoke_then_return
    try:
        response = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers=bearer(token),
            )
        )
    finally:
        DeviceStore.authenticate = real

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_a_device_revoked_after_admission_is_refused_before_serving(
    tmp_path, sample_db
):
    """TOCTOU: a revocation that commits after admission but before the response
    is caught by the active re-check at the serving seam, not served."""
    app, store = build_client_app(tmp_path, sample_db)
    device, token = issue_device(store, "owner desktop")
    real_record = DeviceStore.record_use

    def record_then_revoke(self, device_id, *, now=None):
        count = real_record(self, device_id, now=now)
        # Admitted on a live row; the operator revokes before the response is
        # built.
        self.revoke(device_id)
        return count

    DeviceStore.record_use = record_then_revoke
    try:
        response = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers=bearer(token),
            )
        )
    finally:
        DeviceStore.record_use = real_record

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_is_active_propagates_a_transient_store_error(tmp_path):
    """A store failure on the re-check is surfaced, not swallowed into False.

    Swallowing it (as authenticate deliberately does, for anti-enumeration)
    would misread a locked database as a revoked device and reject a valid one.
    """
    import sqlite3

    store = DeviceStore(tmp_path / "devices.db")
    device, _ = issue_device(store, "owner desktop")

    def boom(self):
        raise sqlite3.OperationalError("database is locked")

    real = DeviceStore._connect
    DeviceStore._connect = boom
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.is_active(device.device_id)
    finally:
        DeviceStore._connect = real


def test_a_transient_recheck_store_error_is_503_not_401(tmp_path, sample_db):
    """A transient store failure on the post-auth re-check must not tell a
    valid device to re-pair: it is the service-unavailable envelope, not
    unauthorized."""
    import sqlite3

    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")
    real = DeviceStore.is_active

    def boom(self, device_id):
        raise sqlite3.OperationalError("database is locked")

    DeviceStore.is_active = boom
    try:
        response = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers=bearer(token),
            )
        )
    finally:
        DeviceStore.is_active = real

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "internal"
    # The one thing it must never be: a credential rejection.
    assert response.json()["error"]["code"] != "unauthorized"


def test_a_transient_record_use_store_error_is_503_not_500(tmp_path, sample_db):
    """A transient failure on the admission write is infrastructure (503), not a
    generic 500 or a credential rejection."""
    import sqlite3

    app, store = build_client_app(tmp_path, sample_db)
    _, token = issue_device(store, "owner desktop")
    real = DeviceStore.record_use

    def boom(self, device_id, *, now=None):
        raise sqlite3.OperationalError("database is locked")

    DeviceStore.record_use = boom
    try:
        response = run(
            call(
                app,
                "POST",
                "/v1/translations",
                json={"text": "声骸"},
                headers=bearer(token),
            )
        )
    finally:
        DeviceStore.record_use = real

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "internal"
