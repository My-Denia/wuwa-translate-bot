"""Tests for wuwaterm_client.credentials using an in-memory fake keyring
backend. The real Windows Credential Manager is never touched by these
tests."""

from __future__ import annotations

import keyring
import keyring.errors
from keyring.backend import KeyringBackend
import pytest

from wuwaterm_client import credentials
from wuwaterm_client.credentials import SERVICE_NAME


class _FakeKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        key = (service, username)
        if key not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[key]


@pytest.fixture()
def fake_keyring():
    previous = keyring.get_keyring()
    backend = _FakeKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


def test_no_token_stored_initially(fake_keyring) -> None:
    assert credentials.read_token() is None
    assert credentials.has_token() is False


def test_store_and_read_token(fake_keyring) -> None:
    credentials.store_token("wtd1.deadbeef.secret")
    assert credentials.read_token() == "wtd1.deadbeef.secret"
    assert credentials.has_token() is True


def test_store_token_overwrites_previous(fake_keyring) -> None:
    credentials.store_token("wtd1.deadbeef.first")
    credentials.store_token("wtd1.deadbeef.second")
    assert credentials.read_token() == "wtd1.deadbeef.second"


def test_delete_token(fake_keyring) -> None:
    credentials.store_token("wtd1.deadbeef.secret")
    credentials.delete_token()
    assert credentials.read_token() is None
    assert credentials.has_token() is False


def test_delete_token_when_absent_does_not_raise(fake_keyring) -> None:
    credentials.delete_token()
    assert credentials.read_token() is None


def test_active_backend_name_reflects_current_backend(fake_keyring) -> None:
    assert credentials.active_backend_name() == "_FakeKeyring"


def test_service_name_is_stable() -> None:
    assert SERVICE_NAME == "WuwaTerm"


def test_a_store_that_cannot_be_read_is_reported_not_raised(monkeypatch) -> None:
    """The vault can be temporarily unavailable.

    A raw backend exception escaped into whichever caller was running: during
    start-up, before a window existed, or from inside a request, where it
    bypassed every view's error handling.
    """
    import keyring
    import keyring.errors

    from wuwaterm_client import credentials

    def explode(*args, **kwargs):
        raise keyring.errors.KeyringError("vault unavailable")

    monkeypatch.setattr(keyring, "get_password", explode)

    with pytest.raises(credentials.CredentialStoreUnavailable):
        credentials.read_token()
    # The question "is one stored" still has an answer.
    assert credentials.has_token() is False


def test_a_store_that_cannot_be_written_is_reported_not_raised(monkeypatch) -> None:
    import keyring
    import keyring.errors

    from wuwaterm_client import credentials

    def explode(*args, **kwargs):
        raise keyring.errors.PasswordSetError("vault unavailable")

    monkeypatch.setattr(keyring, "set_password", explode)

    with pytest.raises(credentials.CredentialStoreUnavailable):
        credentials.store_token("wtd1.device.secret")
