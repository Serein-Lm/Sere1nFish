import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from Sere1nGraph.graph.observability.stats_reader import TokenStatsReader, summarize_rows


def row(kind="scan", model="m", phase="p", calls=2):
    return {"_id": {"task_type": kind, "model": model, "phase": phase, "agent": "a"},
            "calls": calls, "input_tokens": 10, "output_tokens": 3,
            "cost_yuan": 0.001, "duration_ms": 20}


class Collection:
    def __init__(self):
        self.reads = 0
        self.fail = False
        self.data = [row(), row("research", "other")]
        self.matches = []

    def aggregate(self, pipeline, **kwargs):
        self.reads += 1
        self.matches.append(pipeline[0]["$match"])

        async def to_list(_):
            await asyncio.sleep(0)
            if self.fail:
                raise RuntimeError("unavailable")
            return deepcopy(self.data)
        return SimpleNamespace(to_list=to_list)


def test_totals_and_filtered_dimensions_are_preserved():
    data = [row(), row("research", "other", calls=3), {**row(), "_id": {}}]
    stats = summarize_rows(data)
    assert stats["total_calls"] == 7
    assert stats["total_tokens"] == 39
    assert stats["total_duration_ms"] == 60
    assert stats["by_task_type"]["scan"]["calls"] == 2
    assert summarize_rows(data, task_type="research")["by_model"]["other"]["calls"] == 3
    assert summarize_rows(data, phase="missing")["total_calls"] == 0


def test_concurrent_scenarios_and_overview_share_one_read():
    async def run():
        c = Collection()
        reader = TokenStatsReader({"token_usage_records": c})
        overview, scenarios, repeated = await asyncio.gather(reader.stats(), reader.scenarios(), reader.stats())
        assert c.reads == 1
        assert overview == repeated
        assert sum(item["total_tokens"] for item in scenarios.values()) == overview["total_tokens"]
        overview["by_model"]["m"]["calls"] = 999
        assert (await reader.stats())["by_model"]["m"]["calls"] == 2
        assert not reader._locks
    asyncio.run(run())


def test_expiry_invalidation_and_scope_isolation():
    async def run():
        c = Collection()
        reader = TokenStatsReader({"token_usage_records": c}, capacity=2)
        await reader.stats("p1", "t1")
        await reader.stats("p2", "t2")
        assert c.matches == [{"project_id": "p1", "task_id": "t1"}, {"project_id": "p2", "task_id": "t2"}]
        reader.invalidate()
        c.data.append(row(calls=4))
        assert (await reader.stats("p1", "t1"))["total_calls"] == 8
        reader.ttl_seconds = -1
        await reader.stats("p1", "t1")
        await reader.stats("p3", "t3")
        await reader.stats("p4", "t4")
        assert len(reader._cache) == 2
        assert c.reads == 6
    asyncio.run(run())


def test_failed_read_does_not_cache_empty_or_block_recovery():
    async def run():
        c = Collection()
        reader = TokenStatsReader({"token_usage_records": c})
        c.fail = True
        with pytest.raises(RuntimeError):
            await reader.stats()
        assert not reader._cache and not reader._locks
        c.fail = False
        assert (await reader.stats())["total_calls"] == 4
        assert c.reads == 2
    asyncio.run(run())
