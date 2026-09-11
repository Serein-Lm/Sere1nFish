"""Downstream checkpoint barriers retain cancellation and abort semantics."""
import asyncio

import pytest

from core.stream import Pipeline, PipelineAbortError, Stage


@pytest.mark.asyncio
@pytest.mark.parametrize("abort", [False, True])
async def test_drain_propagates_cancel_or_abort_without_leaving_waiters(abort):
    entered = asyncio.Event()
    barrier = asyncio.Event()
    baseline = asyncio.all_tasks()

    class Producer(Stage):
        name = "producer"

        async def handle(self, item, ctx):
            await ctx.emit("persist", item.payload)
            barrier.set()
            await ctx.drain("persist")
            pytest.fail("Unfinished or failed output cannot cross the barrier")

    class Persist(Stage):
        name = "persist"

        async def handle(self, item, ctx):
            entered.set()
            if abort:
                await barrier.wait()
                raise PipelineAbortError("storage fatal")
            await asyncio.Event().wait()

    pipeline = Pipeline(pipeline_id="drain-contract")
    pipeline.add(Producer(), downstream=["persist"]).add(Persist())
    task = asyncio.create_task(pipeline.run(seeds=[1], entry="producer"))
    await asyncio.wait_for(entered.wait(), 1)
    if abort:
        with pytest.raises(PipelineAbortError, match="storage fatal"):
            await asyncio.wait_for(task, 1)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.sleep(0)
    assert not (asyncio.all_tasks() - baseline)


@pytest.mark.asyncio
async def test_drain_rejects_undeclared_downstream():
    class Producer(Stage):
        name = "producer"

        async def handle(self, item, ctx):
            with pytest.raises(ValueError, match="直接下游"):
                await ctx.drain("producer")

    pipeline = Pipeline().add(Producer())
    await pipeline.run(seeds=[1], entry="producer")
