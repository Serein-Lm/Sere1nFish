"""Shared, cancellation-safe resource ordering before durable device leases."""
from __future__ import annotations

import asyncio
import heapq
import itertools
from contextlib import asynccontextmanager
from typing import AsyncIterator


_QUEUE_PRIORITY_ORDER = {
    "high": 0,
    "normal": 10,
    "low": 20,
    "skip": 30,
}
class PriorityExecutionQueue:
    """Serialize one resource while prioritizing queued work without preemption."""

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._active = False
        self._sequence = itertools.count()
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []

    def locked(self) -> bool:
        return self._active

    async def _acquire(self, priority: int) -> None:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] | None = None
        async with self._guard:
            if not self._active:
                self._active = True
                return
            waiter = loop.create_future()
            heapq.heappush(
                self._waiters,
                (priority, next(self._sequence), waiter),
            )

        try:
            await waiter
        except BaseException:
            granted = waiter.done() and not waiter.cancelled()
            if not waiter.done():
                waiter.cancel()
            if granted:
                await self._release()
            raise

    async def _release(self) -> None:
        async with self._guard:
            while self._waiters:
                _priority, _sequence, waiter = heapq.heappop(self._waiters)
                if waiter.done():
                    continue
                waiter.set_result(None)
                return
            self._active = False

    @asynccontextmanager
    async def slot(self, priority: int) -> AsyncIterator[None]:
        await self._acquire(priority)
        try:
            yield
        finally:
            await self._release()


def queue_priority_value(priority: str) -> int:
    return _QUEUE_PRIORITY_ORDER.get(str(priority or "normal").strip().lower(), 10)
