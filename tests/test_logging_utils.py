from __future__ import annotations

import hashlib
import hmac

import pytest

from wuwaterm.logging_utils import (
    REDACTION_SECRET_ENV,
    configure_redaction_secret,
    redact_id,
)


@pytest.fixture(autouse=True)
def reset_redaction_secret(monkeypatch):
    configure_redaction_secret(None)
    monkeypatch.delenv(REDACTION_SECRET_ENV, raising=False)
    yield
    configure_redaction_secret(None)


def test_redact_id_preserves_legacy_hash_without_secret():
    expected = hashlib.sha256(b"123456").hexdigest()[:8]

    assert redact_id(123456) == f"id:{expected}"


def test_redact_id_uses_env_hmac_secret(monkeypatch):
    monkeypatch.setenv(REDACTION_SECRET_ENV, "env-secret")
    expected = hmac.new(b"env-secret", b"123456", hashlib.sha256).hexdigest()[:8]

    redacted = redact_id(123456)

    assert redacted == f"id:{expected}"
    assert "123456" not in redacted
    assert "env-secret" not in redacted


def test_redact_id_runtime_secret_overrides_env(monkeypatch):
    monkeypatch.setenv(REDACTION_SECRET_ENV, "env-secret")
    configure_redaction_secret("runtime-secret")
    expected = hmac.new(b"runtime-secret", b"123456", hashlib.sha256).hexdigest()[:8]

    redacted = redact_id(123456)

    assert redacted == f"id:{expected}"
    assert "runtime-secret" not in redacted


def test_redact_id_changes_with_different_secrets(monkeypatch):
    monkeypatch.setenv(REDACTION_SECRET_ENV, "first-secret")
    first = redact_id(123456)
    monkeypatch.setenv(REDACTION_SECRET_ENV, "second-secret")

    assert redact_id(123456) != first
