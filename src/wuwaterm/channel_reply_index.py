"""Persistent reply tracking for linked-channel translations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Literal


LOGGER = logging.getLogger(__name__)
CHANNEL_REPLY_INDEX_MAX_ENTRIES = 1024
OriginalPostClaimRole = Literal["producer", "waiter", "done"]


@dataclass(frozen=True)
class OriginalPostClaim:
    key: tuple[int, int]
    event: asyncio.Event | None
    role: OriginalPostClaimRole


class ChannelReplyIndex:
    """Maps a forwarded channel post to the bot's translation reply IDs.

    When the linked channel edits a post, Telegram edits the auto-forwarded
    copy in the group in place (same message_id) and the listener fires
    again. This index lets that edit update existing reply chunks instead of
    adding untracked duplicates. Entries are bounded by age (the channel
    freshness window) and can be persisted; if an edit finds no entry, it is
    skipped, degrading to "no update", never to a duplicate reply. The index
    also keeps a process-local sentinel for posts this process observed without
    replying, so a later fresh edit can be treated as the first translatable
    version without reopening the restart duplicate-reply window.
    """

    def __init__(
        self,
        ttl_seconds: float = 300.0,
        storage_path: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        *,
        max_entries: int = CHANNEL_REPLY_INDEX_MAX_ENTRIES,
    ):
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.storage_path = Path(storage_path) if storage_path is not None else None
        self._clock = clock
        self._entries: dict[tuple[int, int], tuple[float, tuple[int, ...]]] = {}
        self._observed_without_reply: dict[tuple[int, int], float] = {}
        self._in_flight: dict[tuple[int, int], asyncio.Event] = {}
        self._latest_edit_tokens: dict[tuple[int, int], int] = {}
        self._edit_delivery_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._next_edit_token = 0
        self._load_failures = 0
        self._last_load_ok: bool | None = None
        self._save_failures = 0
        self._last_save_ok: bool | None = None
        self._load()

    def remember(
        self,
        chat_id: int,
        message_id: int,
        reply_message_id: int,
        now: float | None = None,
    ) -> None:
        self.remember_many(chat_id, message_id, (reply_message_id,), now=now)

    def remember_many(
        self,
        chat_id: int,
        message_id: int,
        reply_message_ids: tuple[int, ...],
        now: float | None = None,
    ) -> None:
        if not reply_message_ids:
            return
        now = self._clock() if now is None else now
        self._entries[(chat_id, message_id)] = (
            now + self.ttl_seconds,
            tuple(reply_message_ids),
        )
        self._observed_without_reply.pop((chat_id, message_id), None)
        self._trim_entries(now)
        self._save_best_effort()

    def get(
        self, chat_id: int, message_id: int, now: float | None = None
    ) -> int | None:
        now = self._clock() if now is None else now
        key = (chat_id, message_id)
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, reply_message_ids = entry
        if now >= expires_at:
            self._forget_key(key, persist=False)
            return None
        return reply_message_ids[0] if reply_message_ids else None

    def get_many(
        self, chat_id: int, message_id: int, now: float | None = None
    ) -> tuple[int, ...]:
        now = self._clock() if now is None else now
        key = (chat_id, message_id)
        entry = self._entries.get(key)
        if entry is None:
            return ()
        expires_at, reply_message_ids = entry
        if now >= expires_at:
            self._forget_key(key, persist=False)
            return ()
        return reply_message_ids

    def forget(self, chat_id: int, message_id: int) -> None:
        self._forget_key((chat_id, message_id), persist=True)

    def remember_observed_without_reply(
        self,
        chat_id: int,
        message_id: int,
        now: float | None = None,
    ) -> None:
        now = self._clock() if now is None else now
        self._observed_without_reply[(chat_id, message_id)] = now + self.ttl_seconds
        self._trim_observed(now)

    def was_observed_without_reply(
        self,
        chat_id: int,
        message_id: int,
        now: float | None = None,
    ) -> bool:
        now = self._clock() if now is None else now
        key = (chat_id, message_id)
        expires_at = self._observed_without_reply.get(key)
        if expires_at is None:
            return False
        if now >= expires_at:
            self._observed_without_reply.pop(key, None)
            return False
        return True

    def entry_count(self, now: float | None = None) -> int:
        self.prune(now=now)
        return len(self._entries)

    def observed_count(self, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        self._trim_observed(now)
        return len(self._observed_without_reply)

    def prune(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        entries_changed = self._trim_entries(now)
        self._trim_observed(now)
        if entries_changed:
            self._save_best_effort()

    def _trim_entries(self, now: float) -> bool:
        before = set(self._entries)
        expired = [
            key for key, entry in self._entries.items() if entry[0] <= now
        ]
        for key in expired:
            self._forget_key(key, persist=False)
        overflow = len(self._entries) - self.max_entries
        if overflow > 0:
            oldest = sorted(
                self._entries,
                key=lambda key: (self._entries[key][0], key[0], key[1]),
            )
            for key in oldest[:overflow]:
                self._forget_key(key, persist=False)
        self._prune_edit_state()
        return set(self._entries) != before

    def _trim_observed(self, now: float) -> None:
        self._observed_without_reply = {
            key: expires_at
            for key, expires_at in self._observed_without_reply.items()
            if expires_at > now
        }
        overflow = len(self._observed_without_reply) - self.max_entries
        if overflow > 0:
            oldest = sorted(
                self._observed_without_reply,
                key=lambda key: (
                    self._observed_without_reply[key],
                    key[0],
                    key[1],
                ),
            )
            for key in oldest[:overflow]:
                self._observed_without_reply.pop(key, None)

    def _forget_key(self, key: tuple[int, int], *, persist: bool) -> None:
        self._entries.pop(key, None)
        self._observed_without_reply.pop(key, None)
        self._latest_edit_tokens.pop(key, None)
        lock = self._edit_delivery_locks.get(key)
        if lock is not None and not lock.locked():
            self._edit_delivery_locks.pop(key, None)
        if persist:
            self._save_best_effort()

    def begin_edit(
        self, chat_id: int, message_id: int, update_id: int | None = None
    ) -> int:
        key = (chat_id, message_id)
        latest = self._latest_edit_tokens.get(key)
        if update_id is None:
            floor = latest if latest is not None else 0
            self._next_edit_token = max(self._next_edit_token, floor) + 1
            token = self._next_edit_token
        else:
            token = update_id
            self._next_edit_token = max(self._next_edit_token, token)
        if latest is None or token > latest:
            self._latest_edit_tokens[key] = token
        return token

    def is_latest_edit(self, chat_id: int, message_id: int, token: int) -> bool:
        return self._latest_edit_tokens.get((chat_id, message_id)) == token

    def edit_delivery_lock(self, chat_id: int, message_id: int) -> asyncio.Lock:
        return self._edit_delivery_locks.setdefault(
            (chat_id, message_id), asyncio.Lock()
        )

    def _prune_edit_state(self) -> None:
        live_keys = set(self._entries)
        self._latest_edit_tokens = {
            key: token
            for key, token in self._latest_edit_tokens.items()
            if key in live_keys
        }
        self._edit_delivery_locks = {
            key: lock
            for key, lock in self._edit_delivery_locks.items()
            if key in live_keys or lock.locked()
        }

    def claim_original(
        self,
        chat_id: int,
        message_id: int,
        *,
        resume_observed: bool = False,
        now: float | None = None,
    ) -> OriginalPostClaim:
        key = (chat_id, message_id)
        event = self._in_flight.get(key)
        if event is not None:
            return OriginalPostClaim(key, event, "waiter")
        if self.get(chat_id, message_id, now=now) is not None:
            return OriginalPostClaim(key, None, "done")
        if not resume_observed and self.was_observed_without_reply(
            chat_id, message_id, now=now
        ):
            return OriginalPostClaim(key, None, "done")
        event = asyncio.Event()
        self._in_flight[key] = event
        return OriginalPostClaim(key, event, "producer")

    async def wait_for_original(self, claim: OriginalPostClaim) -> None:
        if claim.role == "waiter" and claim.event is not None:
            await claim.event.wait()

    async def wait_in_flight(self, chat_id: int, message_id: int) -> bool:
        event = self._in_flight.get((chat_id, message_id))
        if event is None:
            return False
        await event.wait()
        return True

    def finish_original(self, claim: OriginalPostClaim) -> None:
        if claim.role != "producer" or claim.event is None:
            return
        if self._in_flight.get(claim.key) is claim.event:
            self._in_flight.pop(claim.key, None)
        claim.event.set()

    def persistence_enabled(self) -> bool:
        return self.storage_path is not None

    def load_failure_count(self) -> int:
        return self._load_failures

    def last_load_succeeded(self) -> bool | None:
        return self._last_load_ok

    def save_failure_count(self) -> int:
        return self._save_failures

    def last_save_succeeded(self) -> bool | None:
        return self._last_save_ok

    def _record_load_failure(
        self, message: str = "channel reply index unreadable, starting empty"
    ) -> None:
        self._load_failures += 1
        self._last_load_ok = False
        LOGGER.warning(message)

    def _load(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            with self.storage_path.open(encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._record_load_failure()
            return
        if not isinstance(payload, dict):
            self._record_load_failure()
            return
        rows = payload.get("entries")
        if not isinstance(rows, list):
            self._record_load_failure()
            return
        skipped_malformed_rows = False
        now = self._clock()
        for row in rows:
            if not isinstance(row, dict):
                skipped_malformed_rows = True
                continue
            try:
                chat_id = int(row["chat_id"])
                message_id = int(row["message_id"])
                expires_at = float(row["expires_at"])
                reply_ids = tuple(int(item) for item in row["reply_message_ids"])
            except (KeyError, TypeError, ValueError):
                skipped_malformed_rows = True
                continue
            if expires_at <= now or not reply_ids:
                if not reply_ids:
                    skipped_malformed_rows = True
                continue
            self._entries[(chat_id, message_id)] = (expires_at, reply_ids)
        if skipped_malformed_rows:
            self._record_load_failure(
                "channel reply index contained malformed rows"
            )
        else:
            self._last_load_ok = True
        if self._trim_entries(now):
            self._save_best_effort()

    def _save_best_effort(self) -> None:
        if self.storage_path is None:
            return
        try:
            self._save()
        except OSError:
            self._save_failures += 1
            self._last_save_ok = False
            LOGGER.warning("channel reply index save failed")
        else:
            self._last_save_ok = True

    def _save(self) -> None:
        assert self.storage_path is not None
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for (chat_id, message_id), (expires_at, reply_ids) in sorted(
            self._entries.items()
        ):
            rows.append(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "expires_at": expires_at,
                    "reply_message_ids": list(reply_ids),
                }
            )
        payload = {"version": 1, "entries": rows}
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.storage_path.name}.", dir=self.storage_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.storage_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
