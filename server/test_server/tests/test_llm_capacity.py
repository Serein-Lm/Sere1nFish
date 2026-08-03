from __future__ import annotations

import asyncio
import contextvars

import pytest

from core.llm_capacity import (
    LLMCapacityGuard,
    LLMCapacityUnavailableError,
    llm_capacity_priority,
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


@pytest.mark.asyncio
async def test_llm_capacity_release_survives_cross_context_finalization() -> None:
    guard = LLMCapacityGuard(
        max_concurrency=1,
        cooldown_seconds=1,
        max_cooldown_seconds=2,
    )
    lease = guard.lease()
    await lease.__aenter__()

    await asyncio.create_task(
        lease.__aexit__(None, None, None),
        context=contextvars.Context(),
    )

    assert guard.status()["in_use"] == 0
    async with guard.lease() as next_lease:
        assert next_lease["nested"] is False


@pytest.mark.asyncio
async def test_streaming_model_releases_capacity_when_consumer_stops_early(
    monkeypatch,
) -> None:
    from langchain_openai import ChatOpenAI
    from Sere1nGraph.graph.agents import runtime

    guard = LLMCapacityGuard(
        max_concurrency=1,
        cooldown_seconds=1,
        max_cooldown_seconds=2,
    )

    async def fake_stream(_self, *_args, **_kwargs):
        yield "first"
        yield "second"

    monkeypatch.setattr(runtime, "get_global_llm_capacity_guard", lambda: guard)
    monkeypatch.setattr(ChatOpenAI, "_astream", fake_stream)
    model = runtime.GuardedChatOpenAI(model="test", api_key="test")
    stream = model._astream([])

    assert await anext(stream) == "first"
    await asyncio.sleep(0)
    assert guard.status()["in_use"] == 0
    await stream.aclose()


@pytest.mark.asyncio
async def test_interactive_capacity_remains_available_during_standard_load() -> None:
    guard = LLMCapacityGuard(
        max_concurrency=3,
        interactive_reserve=1,
        cooldown_seconds=1,
        max_cooldown_seconds=2,
    )
    started: asyncio.Queue[str] = asyncio.Queue()
    release = asyncio.Event()

    async def run(name: str, priority: str = "standard") -> None:
        with llm_capacity_priority(priority):
            async with guard.lease():
                await started.put(name)
                await release.wait()

    standard_tasks = [
        asyncio.create_task(run(f"standard-{index}"))
        for index in range(3)
    ]
    first = await asyncio.wait_for(started.get(), timeout=1)
    second = await asyncio.wait_for(started.get(), timeout=1)
    assert {first, second}.issubset({f"standard-{index}" for index in range(3)})
    assert guard.status()["standard_waiting"] == 1

    interactive_task = asyncio.create_task(run("interactive", "interactive"))
    assert await asyncio.wait_for(started.get(), timeout=1) == "interactive"
    assert guard.status()["in_use"] == 3
    assert guard.status()["interactive_reserve"] == 1

    release.set()
    await asyncio.gather(*standard_tasks, interactive_task)
    assert guard.status()["in_use"] == 0


def test_assistant_workflow_is_registered_as_interactive() -> None:
    from Sere1nGraph.graph.workflow.registry import get_workflow_capacity_priority

    assert get_workflow_capacity_priority("assistant") == "interactive"
    assert get_workflow_capacity_priority("router") == "standard"
