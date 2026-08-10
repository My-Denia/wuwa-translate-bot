"""Revocable device principals for the HTTP adapter.

Identity here is completely separate from Telegram: a device is its own
principal with its own store (``state/api/devices.db``), so revoking API access
cannot touch the bot's allowlist and vice versa.

Token format (shown exactly once, at issue time)::

    wtd1.<device_id>.<secret>

Only ``sha256(secret)`` is stored. The secret is 32 random bytes from
``secrets.token_urlsafe``, so it is already high-entropy and uniformly
distributed: a password KDF would add cost without adding resistance to a
search over that space. Comparison is constant time.

Revocation sets ``revoked_at`` and keeps the row, so an operator can still see
that a device existed and when it was withdrawn.

The model extends to multi-principal later without changing this schema: the
device id IS the principal id today. The documented trigger for adding a
separate principals table is a second human user or per-user quotas.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


TOKEN_SCHEME = "wtd1"
TOKEN_PARTS = 3
SECRET_BYTES = 32

SCOPE_TRANSLATE = "translate"
SCOPE_META = "meta"
KNOWN_SCOPES = frozenset({SCOPE_TRANSLATE, SCOPE_META})
DEFAULT_SCOPES = (SCOPE_TRANSLATE, SCOPE_META)

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id    TEXT PRIMARY KEY,
    device_name  TEXT NOT NULL,
    token_hash   BLOB NOT NULL,
    scopes       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    revoked_at   TEXT,
    last_used_at TEXT
);
"""


class DeviceStoreError(RuntimeError):
    """Raised for operator-facing device store problems."""


@dataclass(frozen=True)
class Device:
    device_id: str
    device_name: str
    scopes: tuple[str, ...]
    created_at: str
    revoked_at: str | None
    last_used_at: str | None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_secret(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def normalize_scopes(scopes: object) -> tuple[str, ...]:
    if scopes is None:
        return DEFAULT_SCOPES
    if isinstance(scopes, str):
        items = [part.strip() for part in scopes.split(",")]
    else:
        items = [str(part).strip() for part in scopes]
    cleaned = tuple(sorted({item for item in items if item}))
    if not cleaned:
        raise DeviceStoreError("at least one scope is required")
    unknown = sorted(set(cleaned) - KNOWN_SCOPES)
    if unknown:
        known = ", ".join(sorted(KNOWN_SCOPES))
        raise DeviceStoreError(f"unknown scopes {unknown}; known scopes: {known}")
    return cleaned


def parse_token(token: str) -> tuple[str, str] | None:
    """Split a presented token into (device_id, secret), or None if malformed."""
    if not token:
        return None
    parts = token.strip().split(".")
    if len(parts) != TOKEN_PARTS:
        return None
    scheme, device_id, secret = parts
    if scheme != TOKEN_SCHEME or not device_id or not secret:
        return None
    return device_id, secret


class DeviceStore:
    """SQLite-backed device registry. One small file, no server."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- operator commands -------------------------------------------------

    def issue(self, device_name: str, scopes: object = None) -> tuple[Device, str]:
        """Create a device and return it together with its one-time token."""
        name = (device_name or "").strip()
        if not name:
            raise DeviceStoreError("device_name must not be empty")
        resolved = normalize_scopes(scopes)
        device_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        created_at = _now()
        self.initialize()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO devices(device_id, device_name, token_hash, scopes,"
                " created_at, revoked_at, last_used_at)"
                " VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (
                    device_id,
                    name,
                    _hash_secret(secret),
                    ",".join(resolved),
                    created_at,
                ),
            )
        device = Device(
            device_id=device_id,
            device_name=name,
            scopes=resolved,
            created_at=created_at,
            revoked_at=None,
            last_used_at=None,
        )
        return device, f"{TOKEN_SCHEME}.{device_id}.{secret}"

    def list_devices(self) -> list[Device]:
        self.initialize()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM devices ORDER BY created_at, device_id"
            ).fetchall()
        return [_row_to_device(row) for row in rows]

    def revoke(self, device_id: str) -> Device:
        self.initialize()
        revoked_at = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if row is None:
                raise DeviceStoreError(f"unknown device: {device_id}")
            if row["revoked_at"] is None:
                conn.execute(
                    "UPDATE devices SET revoked_at = ? WHERE device_id = ?",
                    (revoked_at, device_id),
                )
                row = conn.execute(
                    "SELECT * FROM devices WHERE device_id = ?", (device_id,)
                ).fetchone()
        return _row_to_device(row)

    # -- request path ------------------------------------------------------

    def authenticate(self, token: str) -> Device | None:
        """Return the live device for ``token``, or None.

        None covers every rejection reason on purpose: unknown device, wrong
        secret, malformed token and revoked device are indistinguishable to the
        caller, so the endpoint cannot be used to enumerate device ids.
        """
        parsed = parse_token(token)
        if parsed is None:
            return None
        device_id, secret = parsed
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if row is None:
                # Still spend a comparison so a missing device and a wrong
                # secret take a similar amount of work.
                hmac.compare_digest(_hash_secret(secret), b"\x00" * 32)
                return None
            if not hmac.compare_digest(_hash_secret(secret), bytes(row["token_hash"])):
                return None
            if row["revoked_at"] is not None:
                return None
            conn.execute(
                "UPDATE devices SET last_used_at = ? WHERE device_id = ?",
                (_now(), device_id),
            )
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
        return _row_to_device(row)


def _row_to_device(row: sqlite3.Row) -> Device:
    return Device(
        device_id=row["device_id"],
        device_name=row["device_name"],
        scopes=tuple(part for part in str(row["scopes"]).split(",") if part),
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
    )
