"""Keyring wrapper for the single device credential this client stores.

The token is never written to disk directly by this application: this
module is the only place that touches it, and it delegates entirely to the
OS credential store via ``keyring`` (Windows Credential Manager on this
platform). config.py never sees the token.
"""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE_NAME = "WuwaTerm"
CREDENTIAL_USERNAME = "device-token"


def store_token(token: str) -> None:
    keyring.set_password(SERVICE_NAME, CREDENTIAL_USERNAME, token)


def read_token() -> str | None:
    return keyring.get_password(SERVICE_NAME, CREDENTIAL_USERNAME)


def has_token() -> bool:
    return read_token() is not None


def delete_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, CREDENTIAL_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass


def active_backend_name() -> str:
    """Name of the keyring backend currently in effect, e.g.
    ``WinVaultKeyring`` on Windows, so the app can report where the
    credential lives."""
    return type(keyring.get_keyring()).__name__
