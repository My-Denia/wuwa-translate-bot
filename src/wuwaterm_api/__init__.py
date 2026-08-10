"""HTTP adapter for the wuwaterm translation service.

A separate top-level package beside ``wuwaterm``: it is one more inbound
adapter, not part of the Telegram bot. It may only reach the shared
application layer (``wuwaterm.application``) plus the protocol-neutral
helpers ``wuwaterm.models``, ``wuwaterm.translation_policy`` and
``wuwaterm.logging_utils``. That narrow allowlist is enforced by
``scripts/check_architecture_boundaries.py`` and is what guarantees the API
cannot bypass the shared translation pipeline.
"""

from __future__ import annotations

__all__ = ["API_VERSION"]

API_VERSION = "v1"
