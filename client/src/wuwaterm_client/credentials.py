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


class CredentialStoreUnavailable(RuntimeError):
    """The OS credential store could not be used.

    The vault can be temporarily unavailable, and the backend then raises from
    wherever it was called: during start-up, before a window exists, or from
    inside a request, where it would bypass every view's error handling and
    leave the status line saying the work is still in progress. Callers get
    this instead, and decide what to show.
    """


# The backend is a native OS component, and it does not confine itself to
# keyring's own exception type: WinVaultKeyring re-raises `pywintypes.error`
# for a CredDelete that fails for any reason other than "not found", and that
# is not a KeyringError. This module is the only boundary to that component,
# and every failure of it means one thing to this application - the store
# could not be used - so every failure is normalized here rather than left to
# surprise a Qt callback.
def store_token(token: str) -> None:
    try:
        keyring.set_password(SERVICE_NAME, CREDENTIAL_USERNAME, token)
    except Exception as exc:
        raise CredentialStoreUnavailable(str(exc)) from exc


def read_token() -> str | None:
    try:
        return keyring.get_password(SERVICE_NAME, CREDENTIAL_USERNAME)
    except Exception as exc:
        raise CredentialStoreUnavailable(str(exc)) from exc


def has_token() -> bool:
    """Whether a credential is stored. A store that cannot be read is not the
    same as an empty one, but for this question it has the same answer."""
    try:
        return read_token() is not None
    except CredentialStoreUnavailable:
        return False


def delete_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, CREDENTIAL_USERNAME)
    except keyring.errors.PasswordDeleteError:
        # There was nothing to delete. Forgetting a credential that is not
        # there is what the caller wanted anyway.
        pass
    except Exception as exc:
        # The vault itself is unavailable, which is a different thing: the
        # credential may still be there and the caller has to be told.
        raise CredentialStoreUnavailable(str(exc)) from exc


def active_backend_name() -> str:
    """Name of the keyring backend currently in effect, e.g.
    ``WinVaultKeyring`` on Windows, so the app can report where the
    credential lives."""
    return type(keyring.get_keyring()).__name__
