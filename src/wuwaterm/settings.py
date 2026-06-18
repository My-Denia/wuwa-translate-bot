"""Per-chat runtime settings, persisted as a tiny JSON file.

Today there is exactly one setting: whether translate commands are open to
all members of a group (the default is admin-only). The file is read once at
startup and rewritten atomically (temp file + os.replace) on every change.
Concurrent writers are not expected — python-telegram-bot drives one
handler at a time in a single event loop — but a process-level lock guards
against future changes that may move work onto worker threads.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from threading import Lock


LOGGER = logging.getLogger(__name__)


class ChatSettings:
    """Per-chat is_public flag with atomic JSON persistence."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = Lock()
        self._public: dict[int, bool] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt or unreadable settings file must NOT crash the bot:
            # the bot keeps running with every chat at the default (closed).
            LOGGER.warning("chat settings unreadable, starting empty: %r", exc)
            return
        raw = data.get("public") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            try:
                self._public[int(key)] = bool(value)
            except (TypeError, ValueError):
                continue

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"public": {str(k): v for k, v in self._public.items()}}
        # Temp file in the same directory so os.replace is atomic on the
        # same filesystem; a half-written file can never become path.
        fd, tmp = tempfile.mkstemp(prefix=".chat_settings.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def is_public(self, chat_id: int) -> bool:
        with self._lock:
            return self._public.get(chat_id, False)

    def set_public(self, chat_id: int, value: bool) -> bool:
        """Set the flag; returns True iff the value actually changed.

        Skips the disk write when value already matches — common case for an
        admin running /public on twice in a row.
        """
        with self._lock:
            if self._public.get(chat_id, False) == value:
                return False
            self._public[chat_id] = value
            self._save()
            return True
