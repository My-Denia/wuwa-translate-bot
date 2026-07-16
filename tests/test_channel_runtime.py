from __future__ import annotations

import asyncio

from wuwaterm.channel_runtime import ChannelRuntime


def test_channel_runtime_bounds_active_and_pending() -> None:
    async def exercise() -> None:
        runtime = ChannelRuntime(
            max_active=1,
            max_pending=1,
            llm_calls_per_minute=10,
        )
        first, reason = runtime.reserve(1)
        second, second_reason = runtime.reserve(1)
        rejected, rejected_reason = runtime.reserve(1)
        assert first is not None and reason is None
        assert second is not None and second_reason is None
        assert rejected is None and rejected_reason == "queue_full"

        entered_second = asyncio.Event()

        async def wait_second() -> None:
            assert second is not None
            async with second:
                entered_second.set()
                snapshot = runtime.snapshot()
                assert snapshot.active == 1
                assert snapshot.pending == 0

        assert first is not None
        async with first:
            first.mark_call_started()
            task = asyncio.create_task(wait_second())
            await asyncio.sleep(0)
            snapshot = runtime.snapshot()
            assert snapshot.active == 1
            assert snapshot.pending == 1
            assert snapshot.high_water == 2
            assert not entered_second.is_set()
        await task
        assert runtime.snapshot().active == 0

    asyncio.run(exercise())


def test_channel_runtime_reserves_multichunk_budget_atomically() -> None:
    async def exercise() -> None:
        now = 100.0
        runtime = ChannelRuntime(
            max_active=1,
            max_pending=1,
            llm_calls_per_minute=2,
            clock=lambda: now,
        )
        rejected, reason = runtime.reserve(3)
        assert rejected is None
        assert reason == "llm_budget"

        lease, reason = runtime.reserve(2)
        assert lease is not None and reason is None
        async with lease:
            lease.mark_call_started()
            lease.mark_call_started()
        rejected, reason = runtime.reserve(1)
        assert rejected is None and reason == "llm_budget"

        now = 161.0
        lease, reason = runtime.reserve(2)
        assert lease is not None and reason is None
        async with lease:
            lease.mark_call_started()
            lease.mark_call_started()

    asyncio.run(exercise())


def test_channel_runtime_cancellation_releases_waiter_and_unused_budget() -> None:
    async def exercise() -> None:
        runtime = ChannelRuntime(
            max_active=1,
            max_pending=1,
            llm_calls_per_minute=2,
        )
        first, _ = runtime.reserve(1)
        assert first is not None
        async with first:
            first.mark_call_started()
            waiting, _ = runtime.reserve(1)
            assert waiting is not None

            async def wait() -> None:
                async with waiting:
                    waiting.mark_call_started()

            task = asyncio.create_task(wait())
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert runtime.snapshot().pending == 0
            replacement, reason = runtime.reserve(1)
            assert replacement is not None and reason is None
        async with replacement:
            replacement.mark_call_started()

    asyncio.run(exercise())


def test_channel_runtime_unused_active_reservation_is_refunded() -> None:
    async def exercise() -> None:
        runtime = ChannelRuntime(
            max_active=1,
            max_pending=0,
            llm_calls_per_minute=2,
        )
        lease, _ = runtime.reserve(2)
        assert lease is not None
        async with lease:
            pass
        replacement, reason = runtime.reserve(2)
        assert replacement is not None and reason is None
        async with replacement:
            replacement.mark_call_started()
            replacement.mark_call_started()

    asyncio.run(exercise())


def test_pending_budget_reservation_survives_more_than_one_minute_wait() -> None:
    async def exercise() -> None:
        now = 0.0
        runtime = ChannelRuntime(
            max_active=1,
            max_pending=1,
            llm_calls_per_minute=2,
            clock=lambda: now,
        )
        first, _ = runtime.reserve(1)
        waiting, _ = runtime.reserve(1)
        assert first is not None and waiting is not None
        waiting_started = asyncio.Event()
        release_waiting = asyncio.Event()

        async def run_waiting() -> None:
            async with waiting:
                waiting.mark_call_started()
                waiting_started.set()
                await release_waiting.wait()

        async with first:
            first.mark_call_started()
            task = asyncio.create_task(run_waiting())
            await asyncio.sleep(0)
            now = 61.0

        await asyncio.wait_for(waiting_started.wait(), timeout=0.2)
        rejected, reason = runtime.reserve(2)
        assert rejected is None
        assert reason == "llm_budget"

        release_waiting.set()
        await asyncio.wait_for(task, timeout=0.2)

    asyncio.run(exercise())


def test_partial_multicall_exit_releases_only_unstarted_tokens() -> None:
    async def exercise() -> None:
        runtime = ChannelRuntime(
            max_active=1,
            max_pending=1,
            llm_calls_per_minute=3,
        )
        lease, reason = runtime.reserve(3)
        assert lease is not None and reason is None
        async with lease:
            lease.mark_call_started()

        replacement, reason = runtime.reserve(2)
        assert replacement is not None and reason is None
        async with replacement:
            replacement.mark_call_started()
            replacement.mark_call_started()

        rejected, reason = runtime.reserve(1)
        assert rejected is None and reason == "llm_budget"

    asyncio.run(exercise())
