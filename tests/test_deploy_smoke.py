from __future__ import annotations

import httpx

from scripts.deploy_smoke import run_smoke
from wuwaterm.logging_utils import REDACTION_SECRET_ENV, redact_id


def test_deploy_smoke_skips_without_token():
    result = run_smoke(token=None, chat_id=None)

    assert result.ok is False
    assert result.sent_message is False
    assert result.lines == ("TELEGRAM_BOT_TOKEN: missing; smoke skipped",)


def test_deploy_smoke_get_me_without_chat_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getMe")
        return httpx.Response(200, json={"ok": True, "result": {"id": 1}})

    result = run_smoke(
        token="test-token",
        chat_id=None,
        api_base="https://telegram.test",
        transport=httpx.MockTransport(handler),
    )

    assert result.ok is True
    assert result.sent_message is False
    assert result.lines == (
        "Bot API getMe: ok",
        "TELEGRAM_TEST_CHAT_ID: missing; sendMessage skipped",
    )
    assert "test-token" not in "\n".join(result.lines)


def test_deploy_smoke_get_me_http_error_is_sanitized(monkeypatch):
    monkeypatch.setenv(REDACTION_SECRET_ENV, "smoke-redaction-secret")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    result = run_smoke(
        token="test-token",
        chat_id="test-chat-id",
        api_base="https://telegram.test",
        transport=httpx.MockTransport(handler),
    )

    joined = "\n".join(result.lines)
    assert result.ok is False
    assert result.sent_message is False
    assert joined == "Bot API getMe: failed"
    assert "test-token" not in joined
    assert "test-chat-id" not in joined


def test_deploy_smoke_can_send_without_printing_chat_id_or_token(monkeypatch):
    monkeypatch.setenv(REDACTION_SECRET_ENV, "smoke-redaction-secret")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1}})
        assert request.url.path.endswith("/sendMessage")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 123}})

    result = run_smoke(
        token="test-token",
        chat_id="test-chat-id",
        api_base="https://telegram.test",
        transport=httpx.MockTransport(handler),
    )

    joined = "\n".join(result.lines)
    assert result.ok is True
    assert result.sent_message is True
    assert f"sendMessage: ok message_id={redact_id(123)}" in joined
    assert "message_id=123" not in joined
    assert "test-token" not in joined
    assert "test-chat-id" not in joined
    assert "smoke-redaction-secret" not in joined
    assert len(requests) == 2


def test_deploy_smoke_send_message_http_error_is_sanitized(monkeypatch):
    monkeypatch.setenv(REDACTION_SECRET_ENV, "smoke-redaction-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"id": 1}})
        return httpx.Response(403)

    result = run_smoke(
        token="test-token",
        chat_id="test-chat-id",
        api_base="https://telegram.test",
        transport=httpx.MockTransport(handler),
    )

    joined = "\n".join(result.lines)
    assert result.ok is False
    assert result.sent_message is False
    assert result.lines == ("Bot API getMe: ok", "sendMessage: failed")
    assert "test-token" not in joined
    assert "test-chat-id" not in joined
    assert "smoke-redaction-secret" not in joined
