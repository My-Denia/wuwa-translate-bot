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
    """Per-chat is_public flags + a group authorization allowlist, atomically persisted."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = Lock()
        self._public: dict[int, bool] = {}
        self._allowed: set[int] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt or unreadable settings file must NOT crash the bot:
            # the bot keeps running with every chat at its default.
            LOGGER.warning("chat settings unreadable, starting empty: %r", exc)
            return
        if not isinstance(data, dict):
            return
        public = data.get("public")
        if isinstance(public, dict):
            for key, value in public.items():
                try:
                    self._public[int(key)] = bool(value)
                except (TypeError, ValueError):
                    continue
        allowed = data.get("allowed")
        if isinstance(allowed, list):
            for item in allowed:
                try:
                    self._allowed.add(int(item))
                except (TypeError, ValueError):
                    continue

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "public": {str(k): v for k, v in self._public.items()},
            "allowed": sorted(self._allowed),
        }
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
            old = self._public.get(chat_id, False)
            if old == value:
                return False
            self._public[chat_id] = value
            try:
                self._save()
            except Exception:
                self._public[chat_id] = old  # keep memory == disk on write failure
                raise
            return True

    def is_allowed(self, chat_id: int) -> bool:
        """True iff the chat is on the group authorization allowlist."""
        with self._lock:
            return chat_id in self._allowed

    def allow(self, chat_id: int) -> bool:
        """Add a chat to the allowlist; returns True iff it changed."""
        with self._lock:
            if chat_id in self._allowed:
                return False
            self._allowed.add(chat_id)
            try:
                self._save()
            except Exception:
                self._allowed.discard(chat_id)
                raise
            return True

    def disallow(self, chat_id: int) -> bool:
        """Remove a chat from the allowlist and persist; returns True iff the
        in-memory set changed.

        Asymmetric with allow() on a write failure, by design. allow() rolls
        back so an un-persisted authorization never grants service (fail-closed
        for GRANTING). disallow() does the opposite: it KEEPS the in-memory
        removal even when the disk write fails (fail-closed for REVOKING), so a
        /revoke whose save failed still denies service this session — is_allowed()
        returns False — instead of silently re-allowing the chat so a re-add or a
        restart could restore it.

        It ALWAYS attempts the save, even when the chat is already absent from
        the in-memory set. That is what lets the owner retry: after a failed save
        the chat is gone from memory but still authorized on disk, so a re-run
        /revoke must rewrite the file rather than short-circuit as a no-op and
        leave the stale on-disk authorization in place. The write failure still
        propagates so the caller can surface it.
        """
        with self._lock:
            changed = chat_id in self._allowed
            self._allowed.discard(chat_id)
            # Always persist (even on a no-op removal) so a /revoke retry after a
            # failed save rewrites the stale file; not rolled back (deny wins).
            self._save()
            return changed

    def allowed_chats(self) -> list[int]:
        with self._lock:
            return sorted(self._allowed)
