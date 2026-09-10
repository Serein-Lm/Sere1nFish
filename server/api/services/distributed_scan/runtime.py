"""Owned maintenance loop for node health and expired distributed leases."""

from __future__ import annotations

import asyncio

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import distributed_work as work_dao
from api.dao import proxy_profiles as proxy_dao
from api.dao import scan_nodes as nodes_dao
from core.logger import get_logger


logger = get_logger("distributed_scan.runtime")


class DistributedScanRuntime:
    _instance: "DistributedScanRuntime | None" = None

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._db: AsyncIOMotorDatabase | None = None

    @classmethod
    def get_instance(cls) -> "DistributedScanRuntime":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self, db: AsyncIOMotorDatabase) -> None:
        if self._task and not self._task.done():
            return
        self._db = db
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(),
            name="distributed-scan-maintenance",
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if not task:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._db = None

    async def sweep_once(self) -> dict[str, int]:
        if self._db is None:
            return {"nodes_offline": 0, "work_requeued": 0, "work_failed": 0, "proxy_expired": 0}
        nodes_offline = await nodes_dao.mark_stale_offline(self._db)
        work = await work_dao.requeue_expired(self._db)
        proxy_expired = await proxy_dao.expire_leases(self._db)
        return {
            "nodes_offline": nodes_offline,
            "work_requeued": work["requeued"],
            "work_failed": work["failed"],
            "proxy_expired": proxy_expired,
        }

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                counts = await self.sweep_once()
                if any(counts.values()):
                    logger.info("分布式扫描租约回收: %s", counts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("分布式扫描维护失败: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
