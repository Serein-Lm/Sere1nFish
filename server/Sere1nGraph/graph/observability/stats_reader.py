"""Bounded, shared read snapshots for historical token statistics.

One grouped read serves global and per-scenario views. No raw usage records are
removed or replaced; snapshots expire after ten seconds and are scoped to DB,
project and task. Concurrent callers share the same in-flight read.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from copy import deepcopy

_DIMENSIONS = ("model", "phase", "agent", "task_type")
_METRICS = ("calls", "input_tokens", "output_tokens", "cost_yuan", "duration_ms")


def summarize_rows(rows: list[dict], **filters: str) -> dict:
    totals = dict.fromkeys(_METRICS, 0)
    buckets = {field: {} for field in _DIMENSIONS}
    for row in rows:
        identity = row.get("_id") or {}
        if any(value and identity.get(key) != value for key, value in filters.items()):
            continue
        for metric in _METRICS:
            totals[metric] += row.get(metric) or 0
        for field in _DIMENSIONS:
            label = identity.get(field)
            if not label:
                continue
            bucket = buckets[field].setdefault(str(label), dict.fromkeys(_METRICS[:-1], 0))
            for metric in bucket:
                bucket[metric] += row.get(metric) or 0
    for group in buckets.values():
        for bucket in group.values():
            bucket["cost_yuan"] = round(bucket["cost_yuan"], 6)
    return {
        **{"total_" + key: totals[key] for key in _METRICS},
        "total_tokens": totals["input_tokens"] + totals["output_tokens"],
        "total_cost_yuan": round(totals["cost_yuan"], 6),
        "total_duration_ms": round(totals["duration_ms"], 1),
        **{"by_" + field: value for field, value in buckets.items()},
    }


class TokenStatsReader:
    def __init__(self, db, *, ttl_seconds: float = 10, capacity: int = 64):
        self.db = db
        self.ttl_seconds = ttl_seconds
        self.capacity = capacity
        self._cache: OrderedDict = OrderedDict()
        self._locks: dict = {}
        self._generation = 0

    def invalidate(self) -> None:
        self._generation += 1
        self._cache.clear()

    async def rows(self, project_id: str = "", task_id: str = "") -> list[dict]:
        key = (project_id, task_id)
        # One lock per active read, removed after the final waiter leaves.
        slot = self._locks.setdefault(key, [asyncio.Lock(), 0])
        slot[1] += 1
        try:
            async with slot[0]:
                cached = self._cache.get(key)
                if cached and time.monotonic() - cached[0] < self.ttl_seconds:
                    self._cache.move_to_end(key)
                    return deepcopy(cached[1])
                generation = self._generation
                query = {k: v for k, v in (("project_id", project_id), ("task_id", task_id)) if v}
                grouped = await self.db["token_usage_records"].aggregate([
                    {"$match": query},
                    {"$group": {
                        "_id": {field: "$" + field for field in _DIMENSIONS},
                        "calls": {"$sum": 1},
                        **{field: {"$sum": "$" + field} for field in _METRICS[1:]},
                    }},
                ], allowDiskUse=True).to_list(None)
                if generation == self._generation:
                    self._cache[key] = (time.monotonic(), grouped)
                    self._cache.move_to_end(key)
                    while len(self._cache) > self.capacity:
                        self._cache.popitem(last=False)
                return deepcopy(grouped)
        finally:
            slot[1] -= 1
            if not slot[1]:
                self._locks.pop(key, None)

    async def stats(self, project_id: str = "", task_id: str = "", **filters: str) -> dict:
        return summarize_rows(await self.rows(project_id, task_id), **filters)

    async def scenarios(self) -> dict[str, dict]:
        rows = await self.rows()
        types = {row.get("_id", {}).get("task_type") for row in rows}
        return {value: summarize_rows(rows, task_type=value) for value in types if value}
