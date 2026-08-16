"""Persistent reply tracking for linked-channel translations."""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Literal

from .channel_reply_schema import (
    CHANNEL_REPLY_INDEX_VERSION,
    ChannelReplyPayloadError,
    parse_channel_reply_payload,
)


LOGGER = logging.getLogger(__name__)
CHANNEL_REPLY_INDEX_MAX_ENTRIES = 1024
OriginalPostClaimRole = Literal["producer", "waiter", "done"]


class ChannelReplyIndexDurabilityError(OSError):
    """The new file is visible but its directory entry may not be durable."""


def _flush_file(file) -> None:
    file.flush()


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
        # token -> (value, expires_at): tokens only matter while an edit is
        # in flight, so they share the entry TTL and are pruned on their own
        # schedule - before that, edits skipped before any remember (content
        # gates) accumulated tokens forever.
        self._latest_edit_tokens: dict[tuple[int, int], tuple[int, float]] = {}
        self._edit_delivery_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._next_edit_token = 0
        self._load_failures = 0
        self._last_load_ok: bool | None = None
        self._save_failures = 0
        self._last_save_ok: bool | None = None
        self._last_save_durable: bool | None = None
        self._pending_save_payload: dict | None = None
        self._save_task: asyncio.Task | None = None
        # Own single worker keeps writes serialized and gives aflush() a real
        # concurrent.futures.Future to wait on: cancelling the asyncio task
        # cannot drop a running write silently, and a job cancelled before it
        # started raises immediately instead of hanging the shutdown flush.
        self._write_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="channel-reply-index-save"
        )
        self._inflight_write: concurrent.futures.Future | None = None
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
        now = self._clock()
        self._prune_edit_tokens(now)
        latest_entry = self._latest_edit_tokens.get(key)
        latest = latest_entry[0] if latest_entry is not None else None
        if update_id is None:
            floor = latest if latest is not None else 0
            self._next_edit_token = max(self._next_edit_token, floor) + 1
            token = self._next_edit_token
        else:
            token = update_id
            self._next_edit_token = max(self._next_edit_token, token)
        if latest is None or token > latest:
            self._latest_edit_tokens[key] = (token, now + self.ttl_seconds)
        return token

    def is_latest_edit(self, chat_id: int, message_id: int, token: int) -> bool:
        entry = self._latest_edit_tokens.get((chat_id, message_id))
        if entry is None:
            return False
        latest, expires_at = entry
        if self._clock() >= expires_at:
            return False
        return latest == token

    def _prune_edit_tokens(self, now: float) -> None:
        self._latest_edit_tokens = {
            key: value
            for key, value in self._latest_edit_tokens.items()
            if value[1] > now
        }
        overflow = len(self._latest_edit_tokens) - self.max_entries
        if overflow > 0:
            oldest = sorted(
                self._latest_edit_tokens,
                key=lambda key: (
                    self._latest_edit_tokens[key][1],
                    key[0],
                    key[1],
                ),
            )
            for key in oldest[:overflow]:
                self._latest_edit_tokens.pop(key, None)

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

    def last_save_durable(self) -> bool | None:
        return self._last_save_durable

    async def aflush(self) -> None:
        """Drain any queued offloaded save, then persist the current snapshot.

        Wired to the application shutdown hook: without it, a remember close
        to process exit can leave its payload queued in the background save
        task while the loop tears down, losing the newest reply ids (duplicate
        translations after restart). Cancellation of the in-flight task is
        deliberately swallowed so the final inline write is best-effort
        guaranteed even during loop teardown.
        """
        if self.storage_path is None:
            return
        task = self._save_task
        if task is not None and not task.done():
            try:
                await task
            except (asyncio.CancelledError, OSError):
                pass
        # A cancelled task does not stop a write already running in the
        # executor: wait for that future before the inline final write, or
        # the older snapshot can replace the newer one on disk afterwards.
        # A job cancelled before it started raises CancelledError from
        # result() instead of hanging here.
        in_flight = self._inflight_write
        if in_flight is not None and not in_flight.done():

            def _wait(future: concurrent.futures.Future) -> None:
                try:
                    future.result()
                except concurrent.futures.CancelledError:
                    pass

            await asyncio.to_thread(_wait, in_flight)
        self._pending_save_payload = None
        self._write_payload_recording(self._build_payload())

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
        try:
            rows, skipped_malformed_rows = parse_channel_reply_payload(
                payload, allow_partial=True
            )
        except ChannelReplyPayloadError:
            self._record_load_failure()
            return
        now = self._clock()
        for row in rows:
            if row.expires_at <= now:
                continue
            self._entries[(row.chat_id, row.message_id)] = (
                row.expires_at,
                row.reply_message_ids,
            )
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
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Sync callers (construction, CLI, tests) keep the original
            # inline semantics: the write has completed when the call returns.
            self._write_payload_recording(self._build_payload())
            return
        # On the bot's event loop the write (tmp file + fsync + replace +
        # directory fsync) must not run inline: remember_many fires once per
        # delivered chunk, so a multi-chunk post would otherwise stall
        # polling, other chats and flood sleeps once per chunk. Snapshot the
        # payload on the loop thread and offload the write, single-flight; a
        # burst coalesces into the latest snapshot instead of queueing one
        # fsync pair per chunk. A save that lands late still holds every row,
        # because the snapshot is taken at the last call before it starts.
        self._pending_save_payload = self._build_payload()
        if self._save_task is None or self._save_task.done():
            self._save_task = loop.create_task(self._save_offloaded())

    async def _save_offloaded(self) -> None:
        while self._pending_save_payload is not None:
            payload = self._pending_save_payload
            self._pending_save_payload = None
            self._inflight_write = self._write_executor.submit(
                self._write_payload_recording, payload
            )
            try:
                await asyncio.wrap_future(self._inflight_write)
            except Exception:
                # The sync path deliberately surfaces only OSError; in a
                # background task anything unexpected would otherwise die as
                # an unretrieved task exception. asyncio.CancelledError is
                # BaseException and propagates to cancel the task.
                LOGGER.exception("channel reply index save failed unexpectedly")

    def _write_payload_recording(self, payload: dict) -> None:
        """Write one snapshot, updating the durability counters.

        May run on an executor thread; the counters are plain int/bool/bool
        assignments, which stay consistent for the /status reader under the
        GIL. Everything it touches besides the counters is immutable
        (``storage_path``) or passed in (``payload``).
        """
        try:
            self._write_payload(payload)
        except ChannelReplyIndexDurabilityError:
            self._save_failures += 1
            self._last_save_ok = True
            self._last_save_durable = False
            LOGGER.warning("channel reply index durability uncertain")
        except OSError:
            self._save_failures += 1
            self._last_save_ok = False
            self._last_save_durable = False
            LOGGER.warning("channel reply index save failed")
        else:
            self._last_save_ok = True
            self._last_save_durable = True

    def _build_payload(self) -> dict:
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
        return {"version": CHANNEL_REPLY_INDEX_VERSION, "entries": rows}

    def _write_payload(self, payload: dict) -> None:
        assert self.storage_path is not None
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.storage_path.name}.", dir=self.storage_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
                _flush_file(f)
                os.fsync(f.fileno())
            os.replace(tmp, self.storage_path)
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            try:
                directory_fd = os.open(self.storage_path.parent, directory_flags)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                raise ChannelReplyIndexDurabilityError() from exc
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
