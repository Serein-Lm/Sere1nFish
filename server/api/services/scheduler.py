"""轻量定时调度器 — 基于 asyncio, 零外部依赖。

- 后台单协程循环, 周期扫描到期调度(schedules.list_due);
- 到期即通过统一任务入口创建并运行一次目标任务(等同手动启动), 随后推进 next_run;
- 支持 interval / cron; enable/disable 由调度记录控制;
- 崩溃隔离: 单个调度异常不影响循环; 随 FastAPI 生命周期 start()/stop()。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from api.db.mongodb import get_db
from api.dao import schedules as schedules_dao
from api.dao import mobile_collect as collect_dao
from core.background import spawn_background
from core.logger import get_logger

logger = get_logger("scheduler")

_SCAN_INTERVAL_SECONDS = 15


class TaskScheduler:
    """统一定时调度器单例。"""

    _instance: "TaskScheduler | None" = None

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

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
        due = await schedules_dao.list_due(db)
        for schedule in due:
            try:
                await self._trigger(db, schedule)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"触发调度失败 schedule={schedule.get('schedule_id')}: {exc}")
            finally:
                # 无论触发成功与否都推进 next_run, 避免持续重触发。
                await schedules_dao.mark_ran(db, schedule["schedule_id"])

    async def _trigger(self, db: Any, schedule: dict[str, Any]) -> None:
        target_type = schedule.get("target_type", "mobile_collect")
        target_id = schedule["target_id"]

        if target_type != "mobile_collect":
            logger.warning(f"不支持的调度目标类型: {target_type}")
            return

        task_def = await collect_dao.get_task_def(db, target_id)
        if not task_def:
            logger.warning(f"调度目标采集任务不存在, 跳过: {target_id}")
            return
        # 目标任务正在运行则跳过本次触发, 避免同设备重叠执行。
        if task_def.get("status") == "running":
            logger.info(f"采集任务运行中, 跳过定时触发: {target_id}")
            return

        # 通过统一任务入口创建并异步运行(等同手动启动)。
        from api.routers.project_api import _execute_task, TASKS_COLLECTION

        project_id = task_def.get("project_id") or ""
        params = {"task_def_id": target_id, "scheduled_by": schedule["schedule_id"]}
        task_id = uuid.uuid4().hex[:12]
        await db[TASKS_COLLECTION].insert_one(
            {
                "task_id": task_id,
                "project_id": project_id,
                "task_type": target_type,
                "params": params,
                "status": "pending",
                "progress": {},
                "trigger": "schedule",
                "schedule_id": schedule["schedule_id"],
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }
        )
        spawn_background(
            _execute_task(task_id, project_id, target_type, params),
            name=f"scheduled:{task_id}",
        )
        logger.notice(
            f"定时触发采集任务 | schedule={schedule['schedule_id']} "
            f"def={target_id} task={task_id}"
        )
