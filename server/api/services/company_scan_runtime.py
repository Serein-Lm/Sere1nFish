"""Resource-aware concurrency for company scans."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.logger import get_logger


logger = get_logger("company_scan_runtime")


class CompanyScanResourcePool:
    """Limit network/AI-heavy phases without counting mobile queue waits."""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self._semaphore = asyncio.Semaphore(self.capacity)
        self.active = 0

    def lease(self, *, task_id: str) -> "CompanyScanCoreLease":
        return CompanyScanCoreLease(pool=self, task_id=task_id)


@dataclass
class CompanyScanCoreLease:
    pool: CompanyScanResourcePool
    task_id: str
    acquired: bool = False

    async def acquire(self) -> None:
        if self.acquired:
            return
        await self.pool._semaphore.acquire()
        self.acquired = True
        self.pool.active += 1
        logger.info(
            "公司扫描核心资源已分配 | task=%s active=%s/%s",
            self.task_id,
            self.pool.active,
            self.pool.capacity,
        )

    def release(self) -> None:
        if not self.acquired:
            return
        self.acquired = False
        self.pool.active = max(0, self.pool.active - 1)
        self.pool._semaphore.release()
        logger.info(
            "公司扫描核心资源已释放 | task=%s active=%s/%s",
            self.task_id,
            self.pool.active,
            self.pool.capacity,
        )


_pool: CompanyScanResourcePool | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None


def get_company_scan_resource_pool(capacity: int) -> CompanyScanResourcePool:
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool is None or _pool_loop is not loop:
        _pool = CompanyScanResourcePool(capacity)
        _pool_loop = loop
    elif _pool.capacity != max(1, int(capacity)):
        logger.warning(
            "公司扫描核心并发变更将在下次进程启动生效 | current=%s requested=%s",
            _pool.capacity,
            capacity,
        )
    return _pool
