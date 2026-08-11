"""Environment-driven settings for the HTTP adapter.

Every knob is read once at startup and validated with an explicit range, so a
typo fails the process instead of silently disabling a limit. Invalid values
are reported without echoing the raw value (it may contain a secret).
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path


class ApiConfigError(ValueError):
    """Raised when the environment cannot produce a usable configuration."""


def validate_loopback_bind(value: str) -> str:
    """Return ``value`` if it is a numeric loopback address, else raise.

    This API is a single-owner surface that must never listen where a remote
    host can reach it. The bind is therefore required to be a numeric loopback
    literal (``127.0.0.1``, any ``127.0.0.0/8`` address, or ``::1``). Refused:

    * ``0.0.0.0`` and ``::`` — every interface, the exact exposure hazard;
    * any routable address — a public or LAN interface;
    * any hostname, including ``localhost`` — it is not numeric and could
      resolve, now or later by DNS the operator does not control, to a
      non-loopback interface. Only an address that is loopback by inspection,
      never by resolution, is accepted.

    The offending value is not echoed: settings never reflect a raw environment
    value back, and the fix is the same regardless of what was set. The returned
    value is the NORMALIZED literal (``str(address)``) so the accepted value is
    the one that actually binds: brackets are URL syntax, not host syntax, and
    ``uvicorn.run(host="[::1]")`` raises at bind time, so ``[::1]`` is returned
    as ``::1`` and surrounding whitespace is dropped.
    """
    candidate = (value or "").strip()
    host = (
        candidate[1:-1]
        if candidate.startswith("[") and candidate.endswith("]")
        else candidate
    )
    if "%" in host:
        # A zone id is accepted by ipaddress (``::1%does-not-exist`` parses AND
        # reports is_loopback), but the scope is not validated here and
        # getaddrinfo fails on a nonexistent one — which would escape as a raw
        # socket error instead of this module's ApiConfigError -> exit 2. The
        # loopback interface needs no scope, so refuse the whole class rather
        # than try to resolve it.
        raise ApiConfigError(
            "the API bind must be a plain numeric loopback address without a "
            "zone id, such as 127.0.0.1 or ::1"
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ApiConfigError(
            "the API bind must be a numeric loopback address such as "
            "127.0.0.1 or ::1"
        ) from None
    if not address.is_loopback:
        raise ApiConfigError(
            "the API bind must be a loopback address such as 127.0.0.1 or ::1; "
            "binding any other interface would expose this single-owner surface"
        )
    return str(address)


MIN_PORT = 1
MAX_PORT = 65535


def validate_port(value: int) -> int:
    """Return ``value`` if it is a usable TCP port, else raise ApiConfigError.

    ``WUWATERM_API_PORT`` is range-checked by ``_env_int``; the ``--port``
    override used to skip that check entirely and go straight to uvicorn, so
    ``--port 999999`` and ``--port -1`` reached the socket layer and escaped as
    a raw error instead of a config error, and ``--port 0`` was silently
    discarded by an ``or`` that treats it as "unset". The override now goes
    through the same range, so every route into the port setting agrees and
    0 is REFUSED explicitly rather than ignored: an operator who asks for an
    ephemeral port is asking for an address the client cannot be configured
    for, and answering "no" is better than answering 8788.
    """
    if not MIN_PORT <= value <= MAX_PORT:
        raise ApiConfigError(
            f"the API port must be between {MIN_PORT} and {MAX_PORT}"
        )
    return value


DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8788
DEFAULT_DB_PATH = "data/terms.db"
# A sibling of the bot's state directory, never a child of it: the bot
# mounts the whole of state/ read-write.
DEFAULT_STATE_DIR = "state-api"
DEVICE_DB_FILENAME = "devices.db"

# Deliberately smaller than the bot's: the API is a single-owner surface and a
# lower ceiling keeps the documented worst-case LLM concurrency small.
DEFAULT_LLM_MAX_CONCURRENCY = 2
DEFAULT_LLM_CALLS_PER_MINUTE = 30
DEFAULT_LLM_TIMEOUT_SECONDS = 45.0
DEFAULT_RATE_LIMIT_PER_MINUTE = 30
DEFAULT_MAX_BODY_BYTES = 32 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 90.0
# Verifying a presented credential costs a deliberate ~16 MiB scrypt
# derivation, and it happens BEFORE any per-device limit can apply. Bound how
# many of those can run at once so an unauthenticated caller cannot turn the
# credential check itself into the load.
DEFAULT_AUTH_MAX_CONCURRENCY = 2


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ApiConfigError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ApiConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ApiConfigError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ApiConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_path(name: str, default: str) -> Path:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return Path(default)
    return Path(raw.strip())


@dataclass(frozen=True)
class ApiSettings:
    db_path: Path
    device_db_path: Path
    # False when an operator named the store explicitly. Only the default
    # layout can have inherited the pre-move path, so only the default layout
    # is checked for one.
    device_db_is_default: bool = True
    bind: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_concurrency: int = DEFAULT_LLM_MAX_CONCURRENCY
    llm_calls_per_minute: int = DEFAULT_LLM_CALLS_PER_MINUTE
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    auth_max_concurrency: int = DEFAULT_AUTH_MAX_CONCURRENCY

    @classmethod
    def from_env(cls) -> "ApiSettings":
        state_dir = _env_path("WUWATERM_API_STATE_DIR", DEFAULT_STATE_DIR)
        device_db = os.getenv("WUWATERM_API_DEVICE_DB_PATH")
        return cls(
            db_path=_env_path("WUWATERM_DB_PATH", DEFAULT_DB_PATH),
            device_db_path=(
                Path(device_db.strip())
                if device_db and device_db.strip()
                else state_dir / DEVICE_DB_FILENAME
            ),
            device_db_is_default=not (device_db and device_db.strip()),
            # Deliberately NOT validated here. from_env() is called by EVERY
            # subcommand, including `device revoke` — gating credential
            # revocation on serve-time network configuration would mean a
            # mistyped bind blocks the one operation that must always work. The
            # loopback guard is applied on the serve path, where a socket is
            # actually bound; see validate_loopback_bind and cli._serve.
            bind=(os.getenv("WUWATERM_API_BIND") or DEFAULT_BIND).strip()
            or DEFAULT_BIND,
            port=_env_int(
                "WUWATERM_API_PORT", DEFAULT_PORT, minimum=MIN_PORT, maximum=MAX_PORT
            ),
            llm_timeout_seconds=_env_float(
                "WUWATERM_API_LLM_TIMEOUT_SECONDS",
                DEFAULT_LLM_TIMEOUT_SECONDS,
                minimum=0.1,
                maximum=300.0,
            ),
            llm_max_concurrency=_env_int(
                "WUWATERM_API_LLM_MAX_CONCURRENCY",
                DEFAULT_LLM_MAX_CONCURRENCY,
                minimum=1,
                maximum=64,
            ),
            llm_calls_per_minute=_env_int(
                "WUWATERM_API_LLM_CALLS_PER_MINUTE",
                DEFAULT_LLM_CALLS_PER_MINUTE,
                minimum=1,
                maximum=10000,
            ),
            rate_limit_per_minute=_env_int(
                "WUWATERM_API_RATE_LIMIT_PER_MINUTE",
                DEFAULT_RATE_LIMIT_PER_MINUTE,
                minimum=1,
                maximum=10000,
            ),
            max_body_bytes=_env_int(
                "WUWATERM_API_MAX_BODY_BYTES",
                DEFAULT_MAX_BODY_BYTES,
                minimum=64,
                maximum=1024 * 1024,
            ),
            request_timeout_seconds=_env_float(
                "WUWATERM_API_REQUEST_TIMEOUT_SECONDS",
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
                minimum=1.0,
                maximum=600.0,
            ),
            auth_max_concurrency=_env_int(
                "WUWATERM_API_AUTH_MAX_CONCURRENCY",
                DEFAULT_AUTH_MAX_CONCURRENCY,
                minimum=1,
                maximum=64,
            ),
        )
