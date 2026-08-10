"""Environment-driven settings for the HTTP adapter.

Every knob is read once at startup and validated with an explicit range, so a
typo fails the process instead of silently disabling a limit. Invalid values
are reported without echoing the raw value (it may contain a secret).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ApiConfigError(ValueError):
    """Raised when the environment cannot produce a usable configuration."""


DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_DB_PATH = "data/terms.db"
DEFAULT_STATE_DIR = "state/api"
DEVICE_DB_FILENAME = "devices.db"

# Deliberately smaller than the bot's: the API is a single-owner surface and a
# lower ceiling keeps the documented worst-case LLM concurrency small.
DEFAULT_LLM_MAX_CONCURRENCY = 2
DEFAULT_LLM_CALLS_PER_MINUTE = 30
DEFAULT_LLM_TIMEOUT_SECONDS = 45.0
DEFAULT_RATE_LIMIT_PER_MINUTE = 30
DEFAULT_MAX_BODY_BYTES = 32 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 90.0


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
    bind: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_concurrency: int = DEFAULT_LLM_MAX_CONCURRENCY
    llm_calls_per_minute: int = DEFAULT_LLM_CALLS_PER_MINUTE
    rate_limit_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS

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
            bind=(os.getenv("WUWATERM_API_BIND") or DEFAULT_BIND).strip()
            or DEFAULT_BIND,
            port=_env_int(
                "WUWATERM_API_PORT", DEFAULT_PORT, minimum=1, maximum=65535
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
        )
