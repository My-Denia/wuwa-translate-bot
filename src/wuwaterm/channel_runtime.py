"""Bounded admission and privacy-safe counters for linked-channel translation."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import itertools
import time
from typing import Callable


@dataclass(frozen=True)
class ChannelRuntimeSnapshot:
    active: int
    pending: int
    high_water: int
    outcomes: dict[str, int]


class ChannelAdmission:
    def __init__(self, runtime: "ChannelRuntime", reservation_id: int):
        self._runtime = runtime
        self._reservation_id = reservation_id
        self._entered = False

    async def __aenter__(self) -> "ChannelAdmission":
        try:
            await self._runtime._semaphore.acquire()
        except BaseException:
            self._runtime._cancel_wait(self._reservation_id)
            raise
        self._runtime._activate()
        self._entered = True
        return self

    def mark_call_started(self) -> None:
        """Consume one reserved token immediately before one LLM call."""
        if not self._entered:
            raise RuntimeError("channel admission is not active")
        self._runtime._mark_call_started(self._reservation_id)

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        if not self._entered:
            return
        self._runtime._release_active(self._reservation_id)
        self._entered = False


class ChannelRuntime:
    """Process-local queue/budget guard and aggregate outcome telemetry.

    ``reserve`` is synchronous by design. PTB tasks execute on one event loop,
    so capacity and all chunk tokens are committed before the first await and
    before any LLM call can start.
    """

    def __init__(
        self,
        *,
        max_active: int,
        max_pending: int,
        llm_calls_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_active < 1:
            raise ValueError("max_active must be at least 1")
        if max_pending < 0:
            raise ValueError("max_pending must be non-negative")
        if llm_calls_per_minute < 1:
            raise ValueError("llm_calls_per_minute must be at least 1")
        self.max_active = max_active
        self.max_pending = max_pending
        self.llm_calls_per_minute = llm_calls_per_minute
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max_active)
        self._active = 0
        self._pending = 0
        self._high_water = 0
        self._outcomes: Counter[str] = Counter()
        self._reservation_ids = itertools.count(1)
        self._budget_tokens: list[tuple[int, float, bool]] = []

    def reserve(self, calls: int) -> tuple[ChannelAdmission | None, str | None]:
        if calls < 1:
            raise ValueError("calls must be at least 1")
        self._prune_budget()
        if self._active + self._pending >= self.max_active + self.max_pending:
            return None, "queue_full"
        if len(self._budget_tokens) + calls > self.llm_calls_per_minute:
            return None, "llm_budget"
        reservation_id = next(self._reservation_ids)
        reserved_at = self._clock()
        self._budget_tokens.extend(
            (reservation_id, reserved_at, False) for _ in range(calls)
        )
        self._pending += 1
        self._high_water = max(self._high_water, self._active + self._pending)
        return ChannelAdmission(self, reservation_id), None

    def record(self, stage: str, reason: str) -> None:
        self._outcomes[f"{stage}:{reason}"] += 1

    def snapshot(self) -> ChannelRuntimeSnapshot:
        return ChannelRuntimeSnapshot(
            active=self._active,
            pending=self._pending,
            high_water=self._high_water,
            outcomes=dict(sorted(self._outcomes.items())),
        )

    def _prune_budget(self) -> None:
        cutoff = self._clock() - 60.0
        self._budget_tokens = [
            token
            for token in self._budget_tokens
            if not token[2] or token[1] > cutoff
        ]

    def _cancel_wait(self, reservation_id: int) -> None:
        self._pending = max(0, self._pending - 1)
        self._release_budget(reservation_id)

    def _activate(self) -> None:
        self._pending = max(0, self._pending - 1)
        self._active += 1
        self._high_water = max(self._high_water, self._active + self._pending)

    def _mark_call_started(self, reservation_id: int) -> None:
        started_at = self._clock()
        updated: list[tuple[int, float, bool]] = []
        consumed = False
        for token_id, timestamp, started in self._budget_tokens:
            if token_id == reservation_id and not started and not consumed:
                updated.append((token_id, started_at, True))
                consumed = True
            else:
                updated.append((token_id, timestamp, started))
        if not consumed:
            raise RuntimeError("channel admission has no unused call reservation")
        self._budget_tokens = updated

    def _release_active(self, reservation_id: int) -> None:
        self._budget_tokens = [
            token
            for token in self._budget_tokens
            if token[0] != reservation_id or token[2]
        ]
        self._active = max(0, self._active - 1)
        self._semaphore.release()

    def _release_budget(self, reservation_id: int) -> None:
        self._budget_tokens = [
            token for token in self._budget_tokens if token[0] != reservation_id
        ]
