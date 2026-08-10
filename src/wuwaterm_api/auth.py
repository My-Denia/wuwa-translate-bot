"""Revocable device principals for the HTTP adapter.

Identity here is completely separate from Telegram: a device is its own
principal with its own store (``state/api/devices.db``), so revoking API access
cannot touch the bot's allowlist and vice versa.

Token format::

    wtd1.<device_id>.<secret>

**This service never produces or emits a secret.** The operator supplies the
secret on standard input when issuing a device, and only a derived verifier is
stored. That keeps every credential-bearing byte out of the server process'
output, out of terminal scrollback and out of anything that could capture,
format or persist it — a property worth more than the small convenience of
having the server mint the value for you.

The secret's character set is unconstrained (it may contain dots, which the
token parser accounts for); only surrounding whitespace is refused, because a
presented token is stripped as a whole.

Because the operator chooses the secret, this store cannot assume the entropy
a machine-generated value would have had. It therefore refuses anything shorter
than ``MIN_SECRET_LENGTH`` **and** derives the stored value with ``scrypt`` and
a per-device salt rather than a bare digest, so a stolen store cannot be
searched cheaply even if an operator picked something guessable. Comparison is
constant time.

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
# A supplied secret must have at least this much material. 32 URL-safe
# characters is roughly 190 bits, far past what a hash-only store needs.
MIN_SECRET_LENGTH = 32

SCOPE_TRANSLATE = "translate"
SCOPE_META = "meta"
KNOWN_SCOPES = frozenset({SCOPE_TRANSLATE, SCOPE_META})
DEFAULT_SCOPES = (SCOPE_TRANSLATE, SCOPE_META)

# scrypt parameters. 2**14 blocks with r=8 is ~16 MiB and a few tens of
# milliseconds per verification: comfortably inside a per-device request budget
# measured in tens per minute, and expensive enough that a stolen store is not
# worth grinding.
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id    TEXT PRIMARY KEY,
    device_name  TEXT NOT NULL,
    salt         BLOB NOT NULL,
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


def _derive(secret: str, salt: bytes) -> bytes:
    """Derive the stored verifier for an operator-chosen secret."""
    return hashlib.scrypt(
        secret.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )


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
    """Split a presented token into (device_id, secret), or None if malformed.

    The split is bounded at two separators so the secret's own character set
    stays unconstrained: an operator-chosen secret may contain dots (base64,
    PEM-ish and UUID-ish material all do) and must still round-trip.
    """
    if not token:
        return None
    parts = token.strip().split(".", TOKEN_PARTS - 1)
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
        existed = self.path.exists()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # CREATE TABLE IF NOT EXISTS cannot add a column to a store written
            # by an older shape. Say so plainly instead of failing later with a
            # confusing SQL error.
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(devices)")
            }
            missing = {"salt", "token_hash"} - columns
            if missing:
                raise DeviceStoreError(
                    f"{self.path} was written by an older device store and is "
                    f"missing {sorted(missing)}; remove the file and register "
                    f"the devices again"
                )
        if not existed:
            # Credential material, even hashed, is not world-readable.
            try:
                self.path.chmod(0o600)
            except OSError:  # pragma: no cover - filesystem without POSIX modes
                pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- operator commands -------------------------------------------------

    def issue(
        self, device_name: str, scopes: object = None, *, secret: str
    ) -> Device:
        """Register a device for an operator-supplied secret.

        Returns the device only. The secret is never echoed back, so no caller
        of this method can accidentally route it to output.
        """
        name = (device_name or "").strip()
        if not name:
            raise DeviceStoreError("device_name must not be empty")
        # Taken verbatim: the secret's character set is deliberately
        # unconstrained. Surrounding whitespace is refused rather than trimmed,
        # because a presented token is stripped as a whole and a silently
        # trimmed secret would register fine and then never authenticate.
        material = secret or ""
        if material != material.strip():
            raise DeviceStoreError(
                "the supplied secret must not begin or end with whitespace"
            )
        if len(material) < MIN_SECRET_LENGTH:
            raise DeviceStoreError(
                f"the supplied secret must be at least {MIN_SECRET_LENGTH}"
                " characters of unguessable material"
            )
        resolved = normalize_scopes(scopes)
        device_id = secrets.token_hex(8)
        created_at = _now()
        salt = secrets.token_bytes(SALT_BYTES)
        self.initialize()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO devices(device_id, device_name, salt, token_hash,"
                " scopes, created_at, revoked_at, last_used_at)"
                " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    device_id,
                    name,
                    salt,
                    _derive(material, salt),
                    ",".join(resolved),
                    created_at,
                ),
            )
        return Device(
            device_id=device_id,
            device_name=name,
            scopes=resolved,
            created_at=created_at,
            revoked_at=None,
            last_used_at=None,
        )

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
        """Return the live device for ``token``, or None. READ ONLY.

        None covers every rejection reason on purpose: unknown device, wrong
        secret, malformed token, revoked device and an unusable store are all
        indistinguishable to the caller, so the endpoint cannot be used to
        enumerate device ids or to probe the server's state. An operator who
        needs to know WHY a store is unusable gets that at startup, where the
        message has an audience.

        Usage is deliberately NOT recorded here: a caller that is about to be
        refused by a rate limit must not be able to drive an unbounded stream
        of writes. Call :meth:`record_use` once the request is admitted.
        """
        parsed = parse_token(token)
        if parsed is None:
            return None
        device_id, secret = parsed
        try:
            self.initialize()
        except DeviceStoreError:
            # Loud at startup, uniform on the request path.
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
            if row is None:
                # Still spend a derivation so a missing device and a wrong
                # secret take a similar amount of work.
                hmac.compare_digest(
                    _derive(secret, b"\x00" * SALT_BYTES), b"\x00" * SCRYPT_DKLEN
                )
                return None
            if not hmac.compare_digest(
                _derive(secret, bytes(row["salt"])), bytes(row["token_hash"])
            ):
                return None
            if row["revoked_at"] is not None:
                return None
        return _row_to_device(row)

    def record_use(self, device_id: str, *, now: str | None = None) -> None:
        """Stamp last_used_at for an ADMITTED request."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET last_used_at = ? WHERE device_id = ?"
                " AND revoked_at IS NULL",
                (now or _now(), device_id),
            )


def _row_to_device(row: sqlite3.Row) -> Device:
    return Device(
        device_id=row["device_id"],
        device_name=row["device_name"],
        scopes=tuple(part for part in str(row["scopes"]).split(",") if part),
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
    )
