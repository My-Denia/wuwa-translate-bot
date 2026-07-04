"""Small helpers for privacy-safe operational logs."""

from __future__ import annotations

import hashlib
from typing import Any


def redact_id(value: Any) -> str:
    if value is None:
        return "none"
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
    return f"id:{digest}"


def safe_text_len(text: str | None) -> int:
    return len(text or "")


def safe_error_type(exc: BaseException) -> str:
    return type(exc).__name__
