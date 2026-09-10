"""轻量定时调度器 — 基于 asyncio, 零外部依赖。

- 后台单协程循环, 周期扫描到期调度(schedules.list_due);
- 到期即通过统一任务入口创建并运行一次目标任务(等同手动启动), 随后推进 next_run;
- 支持 interval / cron; enable/disable 由调度记录控制;
- 崩溃隔离: 单个调度异常不影响循环; 随 FastAPI 生命周期 start()/stop()。
"""
from __future__ import annotations

import asyncio
import socket
import uuid

from api.db.mongodb import get_db
from api.dao import schedules as schedules_dao
from api.services.scheduling import trigger_schedule
from core.logger import get_logger

logger = get_logger("scheduler")

_SCAN_INTERVAL_SECONDS = 15
_MAX_CLAIMS_PER_SCAN = 50
_CLAIM_LEASE_SECONDS = 120


class TaskScheduler:
    """统一定时调度器单例。"""

    _instance: "TaskScheduler | None" = None

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lease_owner = f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"

    @classmethod
    def get_instance(cls) -> "TaskScheduler":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="task-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        logger.info("定时调度器已启动")
        while not self._stop.is_set():
            try:
                await self._scan_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"调度扫描异常(不影响循环): {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=_SCAN_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
        logger.info("定时调度器已停止")

    async def _scan_once(self) -> None:
        db = get_db()
        for _ in range(_MAX_CLAIMS_PER_SCAN):
            schedule = await schedules_dao.claim_due(
                db,
                lease_owner=self._lease_owner,
                lease_seconds=_CLAIM_LEASE_SECONDS,
            )
            if not schedule:
                break
            status = "failed"
            error = ""
            try:
                result = await trigger_schedule(db, schedule)
                status = result.status
                if result.message:
                    logger.info(
                        "调度结果 schedule=%s status=%s message=%s",
                        schedule.get("schedule_id"),
                        result.status,
                        result.message,
                    )
                if result.task_id:
                    logger.notice(
                        "定时触发采集任务 | schedule=%s task=%s",
                        schedule.get("schedule_id"),
                        result.task_id,
                    )
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                logger.warning(
                    "触发调度失败 schedule=%s: %s",
                    schedule.get("schedule_id"),
                    exc,
                )
            finally:
                # Every claim advances once, including busy/failed attempts, so a
                # broken target cannot be retriggered every 15 seconds.
                await schedules_dao.complete_claim(
                    db,
                    schedule_id=str(schedule.get("schedule_id") or ""),
                    lease_owner=self._lease_owner,
                    status=status,
                    error=error,
                )
