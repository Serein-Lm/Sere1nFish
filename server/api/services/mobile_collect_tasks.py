"""Unified lifecycle entry points for persisted mobile collection tasks."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import mobile_collect as collect_dao
from api.dao import tasks as tasks_dao
from api.services.project_task_runtime import execute_project_task
from core.background import spawn_background


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
    task_def = await collect_dao.get_task_def(db, task_def_id)
    if not task_def:
        raise MobileCollectTaskNotFoundError("采集任务定义不存在")
    if task_def.get("status") == "running":
        raise MobileCollectTaskBusyError("该采集任务正在运行中")

    project_id = str(task_def.get("project_id") or "")
    task_id = uuid.uuid4().hex[:12]
    params = {"task_def_id": task_def_id}
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
