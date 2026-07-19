from __future__ import annotations

import asyncio

import pytest

from core.llm_capacity import (
    LLMCapacityGuard,
    LLMCapacityUnavailableError,
)


@pytest.mark.asyncio
async def test_llm_capacity_guard_bounds_concurrent_work() -> None:
    guard = LLMCapacityGuard(
        max_concurrency=2,
        cooldown_seconds=1,
        max_cooldown_seconds=2,
    )
    active = 0
    peak = 0

    async def run() -> None:
        nonlocal active, peak
        async with guard.lease():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(run() for _ in range(8)))

    assert peak == 2
    assert guard.status()["in_use"] == 0


@pytest.mark.asyncio
async def test_llm_capacity_guard_opens_and_recovers_with_one_probe() -> None:
    now = 100.0

    def clock() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        now += delay
        await asyncio.sleep(0)

    guard = LLMCapacityGuard(
        max_concurrency=3,
        cooldown_seconds=5,
        max_cooldown_seconds=20,
        clock=clock,
        sleep=sleep,
    )

    with pytest.raises(LLMCapacityUnavailableError) as raised:
        async with guard.lease():
            raise RuntimeError("429 insufficient_quota token-limit")

    assert raised.value.incident_id == 1
    assert guard.status()["circuit_open"] is True

    async with guard.lease() as lease:
        assert lease["is_probe"] is True

    assert guard.status()["circuit_open"] is False
    assert guard.status()["failure_streak"] == 0


@pytest.mark.asyncio
async def test_nested_llm_lease_reuses_outer_slot() -> None:
    guard = LLMCapacityGuard(
        max_concurrency=1,
        cooldown_seconds=1,
        max_cooldown_seconds=2,
    )

    async with guard.lease() as outer:
        async with guard.lease() as nested:
            assert outer["nested"] is False
            assert nested["nested"] is True
            assert guard.status()["in_use"] == 1
