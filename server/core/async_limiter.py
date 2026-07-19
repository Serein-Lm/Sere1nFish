"""Event-loop-local resizable concurrency limiter."""
from __future__ import annotations

import asyncio
from collections import deque


class ResizableLimiter:
    """Semaphore-compatible limiter whose capacity can change at runtime."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._in_use = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def waiting(self) -> int:
        return sum(1 for waiter in self._waiters if not waiter.done())

    def locked(self) -> bool:
        return self._in_use >= self._limit

    async def acquire(self) -> None:
        if self._in_use < self._limit and not self._waiters:
            self._in_use += 1
            return

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        except BaseException:
            granted = waiter.done() and not waiter.cancelled()
            if not waiter.done():
                waiter.cancel()
            if granted:
                self.release()
            raise

    def release(self) -> None:
        if self._in_use <= 0:
            raise ValueError("ResizableLimiter released too many times")
        self._in_use -= 1
        self._wake_waiters()

    def resize(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._wake_waiters()

    def _wake_waiters(self) -> None:
        while self._in_use < self._limit and self._waiters:
            waiter = self._waiters.popleft()
            if waiter.done():
                continue
            self._in_use += 1
            waiter.set_result(None)
