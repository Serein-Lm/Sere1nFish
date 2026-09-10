"""Unified project-task submission service."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import projects as projects_dao
from api.dao import tasks as tasks_dao
from api.services.project_task_runtime import execute_project_task
from api.services.project_tasks.registry import (
    get_project_task_definition,
    list_project_task_definitions,
)
from api.services.project_tasks.validation import prepare_project_task_params
from core.background import spawn_background


class UnsupportedProjectTaskError(ValueError):
    pass


class ProjectNotFoundError(LookupError):
    pass


def list_project_task_types() -> list[dict[str, object]]:
    return [definition.public_view() for definition in list_project_task_definitions()]


async def submit_project_task(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    task_type: str,
    params: dict[str, Any] | None,
    requested_by: str,
    file_text: str | None = None,
) -> dict[str, str]:
    """Validate, persist and schedule one task through the registered capability."""
    definition = get_project_task_definition(task_type)
    if definition is None:
        raise UnsupportedProjectTaskError(f"不支持的 task_type: {task_type}")
    if not await projects_dao.get_project(db, project_id):
        raise ProjectNotFoundError("项目不存在")

    submitted_params = dict(params or {})
    if file_text is not None:
        if definition.file_field is None:
            raise UnsupportedProjectTaskError(
                f"任务类型 {task_type} 不支持文件输入"
            )
        submitted_params[definition.file_field] = file_text
    normalized_params = await prepare_project_task_params(
        db,
        project_id=project_id,
        task_type=definition.task_type,
        params=submitted_params,
    )

    task_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    await tasks_dao.insert_task(
        db,
        {
            "task_id": task_id,
            "project_id": project_id,
            "task_type": definition.task_type,
            "params": normalized_params,
            "requested_by": requested_by,
            "status": "pending",
            "progress": {},
            "created_at": now,
            "updated_at": now,
        },
    )
    runtime_params = {**normalized_params, "_requested_by": requested_by}
    spawn_background(
        execute_project_task(
            task_id,
            project_id,
            definition.task_type,
            runtime_params,
        ),
        name=f"task:{task_id}",
    )
    return {
        "task_id": task_id,
        "task_type": definition.task_type,
        "status": "pending",
    }
