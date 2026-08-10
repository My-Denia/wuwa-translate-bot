"""Non-secret client settings, persisted as JSON under the per-user app data
directory.

The device token is never stored here, and never held by this module at
all: see credentials.py, which is the only place that touches it and keeps
it exclusively in the OS credential store.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
from pathlib import Path

APP_DIR_NAME = "WuwaTerm"
CONFIG_FILE_NAME = "config.json"

DEFAULT_BASE_URL = "http://127.0.0.1:8787"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_TRANSLATE_TIMEOUT_SECONDS = 60.0
# The same bounds the Settings dialog enforces. A hand-edited config file is
# the only way a value outside them can arrive, and "never raises" must not
# mean "passes a zero or a string straight into the HTTP client".
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 600.0


def app_data_dir() -> Path:
    """Per-user app data directory. ``%APPDATA%/WuwaTerm`` on Windows."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def config_path(base_dir: Path | None = None) -> Path:
    return (base_dir if base_dir is not None else app_data_dir()) / CONFIG_FILE_NAME


def _sane_timeout(value: object, fallback: float) -> float:
    """A timeout from disk, clamped, or the default if it is not a number.

    Python's JSON parser accepts `NaN` and `Infinity`, and `min`/`max` pass
    NaN straight through, so a non-finite value would reach httpx as a
    deadline that never expires. Finiteness is checked, not assumed.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    number = float(value)
    if not math.isfinite(number):
        return fallback
    return float(min(max(number, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))


def _sane_base_url(value: object, fallback: str) -> str:
    """Annotations are not runtime validation: a list here would reach httpx."""
    if not isinstance(value, str) or not value.strip():
        return fallback
    return value.strip()


@dataclasses.dataclass(frozen=True)
class ClientConfig:
    base_url: str = DEFAULT_BASE_URL
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    translate_timeout_seconds: float = DEFAULT_TRANSLATE_TIMEOUT_SECONDS

    @classmethod
    def load(cls, base_dir: Path | None = None) -> "ClientConfig":
        """Load from disk, falling back to defaults for anything missing,
        unreadable, malformed, or unrecognized. Never raises."""
        path = config_path(base_dir)
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known_fields = {field.name for field in dataclasses.fields(cls)}
        filtered = {key: value for key, value in raw.items() if key in known_fields}
        defaults = cls()
        for name, fallback in (
            ("request_timeout_seconds", defaults.request_timeout_seconds),
            ("translate_timeout_seconds", defaults.translate_timeout_seconds),
        ):
            if name in filtered:
                filtered[name] = _sane_timeout(filtered[name], fallback)
        if "base_url" in filtered:
            filtered["base_url"] = _sane_base_url(
                filtered["base_url"], defaults.base_url
            )
        try:
            return cls(**filtered)
        except TypeError:
            return cls()

    def save(self, base_dir: Path | None = None) -> None:
        path = config_path(base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dataclasses.asdict(self)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
