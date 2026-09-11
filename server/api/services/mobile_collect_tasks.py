"""Unified lifecycle entry points for persisted mobile collection tasks."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from weakref import WeakKeyDictionary

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import mobile_collect as collect_dao
from api.dao import tasks as tasks_dao
from api.services.project_task_runtime import execute_project_task
from core.background import spawn_background


_START_LOCKS: WeakKeyDictionary = WeakKeyDictionary()
_ACTIVE_RUN_STATUSES = ["pending", "running", "pausing"]


class MobileCollectTaskNotFoundError(ValueError):
    pass


class MobileCollectTaskBusyError(RuntimeError):
    pass


async def start_mobile_collect_task(
    db: AsyncIOMotorDatabase,
    *,
    task_def_id: str,
    requested_by: str = "",
    trigger: str = "manual",
    schedule_id: str = "",
) -> dict[str, Any]:
    """Create and dispatch one project task from a persisted definition."""
    locks = _START_LOCKS.setdefault(asyncio.get_running_loop(), {})
    lock = locks.setdefault(task_def_id, asyncio.Lock())
    async with lock:
        return await _start_mobile_collect_task(
            db, task_def_id=task_def_id, requested_by=requested_by,
            trigger=trigger, schedule_id=schedule_id,
        )


async def _start_mobile_collect_task(
    db: AsyncIOMotorDatabase, *, task_def_id: str, requested_by: str,
    trigger: str, schedule_id: str,
) -> dict[str, Any]:
    task_def = await collect_dao.get_task_def(db, task_def_id)
    if not task_def:
        raise MobileCollectTaskNotFoundError("采集任务定义不存在")
    if task_def.get("status") == "running":
        raise MobileCollectTaskBusyError("该采集任务正在运行中")

    project_id = str(task_def.get("project_id") or "")
    active = await tasks_dao.find_latest_matching_task(
        db, project_id=project_id, task_type="mobile_collect",
        param_filters={"task_def_id": task_def_id}, statuses=_ACTIVE_RUN_STATUSES,
        projection={"_id": 0, "task_id": 1},
    )
    if active:
        raise MobileCollectTaskBusyError("该采集任务已在等待或执行中，请查看当前运行进度")
    task_id = uuid.uuid4().hex[:12]
    params = {
        "task_def_id": task_def_id,
        "queue_priority": str(task_def.get("queue_priority") or "normal"),
    }
    if requested_by:
        params["_requested_by"] = requested_by
    if schedule_id:
        params["scheduled_by"] = schedule_id
    now = datetime.now(timezone.utc)
    document: dict[str, Any] = {
        "task_id": task_id,
        "project_id": project_id,
        "task_type": "mobile_collect",
        "params": params,
        "status": "pending",
        "progress": {},
        "trigger": trigger,
        "created_at": now,
        "updated_at": now,
    }
    if requested_by:
        document["requested_by"] = requested_by
    if schedule_id:
        document["schedule_id"] = schedule_id
    await tasks_dao.insert_task(db, document)
    spawn_background(
        execute_project_task(task_id, project_id, "mobile_collect", params),
        name=f"mobile_collect:{task_id}",
    )
    return {
        "task_id": task_id,
        "task_def_id": task_def_id,
        "project_id": project_id,
        "status": "pending",
        "trigger": trigger,
    }


async def list_mobile_collect_tasks(
    db: AsyncIOMotorDatabase, *, project_id: str | None = None,
) -> list[dict[str, Any]]:
    definitions = await collect_dao.list_task_defs(db, project_id=project_id)
    runs = await tasks_dao.latest_mobile_collect_runs(db, [item["task_def_id"] for item in definitions])
    return [{**item, "latest_run": runs.get(item["task_def_id"])} for item in definitions]


async def stop_mobile_collect_task(
    db: AsyncIOMotorDatabase, *, task_def_id: str, run_task_id: str | None = None,
) -> dict[str, Any]:
    """Persist stop intent, including work that has not acquired a phone yet."""
    definition = await collect_dao.get_task_def(db, task_def_id)
    if not definition:
        raise MobileCollectTaskNotFoundError("采集任务定义不存在")
    project_id = str(definition.get("project_id") or "")
    current = await tasks_dao.find_latest_matching_task(
        db, project_id=project_id, task_type="mobile_collect",
        param_filters={"task_def_id": task_def_id}, statuses=_ACTIVE_RUN_STATUSES + ["paused"],
    )
    selected = str(run_task_id or (current or {}).get("task_id") or definition.get("last_run_task_id") or "")
    if not selected:
        raise ValueError("没有可停止的运行实例")
    if current and selected == current.get("task_id"):
        from api.services.project_task_control import pause_project_task

        paused = await pause_project_task(db, project_id=project_id, task_id=selected)
        return {"ok": True, "run_task_id": selected, "status": paused.get("status")}
    if selected != definition.get("last_run_task_id"):
        raise ValueError("运行实例不属于该采集任务")
    from core.mobile.collect import request_stop

    return {"ok": request_stop(selected), "run_task_id": selected}
