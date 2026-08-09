"""Manual pause and resume orchestration for persistent project tasks."""
from __future__ import annotations

from typing import Any

from api.dao import tasks as tasks_dao
from api.services.project_task_runtime import (
    build_task_runtime_params,
    cancel_running_project_task,
    execute_project_task,
    supported_task_types,
)
from core.background import spawn_background
from core.logger import get_logger
from core.observability import obs_log


logger = get_logger("project_task_control")
_TERMINAL_STATUSES = {"completed", "error", "failed", "cancelled"}


class ProjectTaskNotFoundError(LookupError):
    pass


class ProjectTaskStateError(ValueError):
    pass


async def pause_project_task(
    db: Any,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    document = await tasks_dao.request_task_pause(
        db,
        project_id=project_id,
        task_id=task_id,
    )
    if not document:
        raise ProjectTaskNotFoundError("任务不存在")

    status = str(document.get("status") or "")
    if status in _TERMINAL_STATUSES:
        raise ProjectTaskStateError("已结束的任务不能暂停")
    if status not in {"paused", "pausing"}:
        raise ProjectTaskStateError(f"任务当前状态不支持暂停: {status or 'unknown'}")

    if status == "pausing":
        cancelled = await cancel_running_project_task(task_id)
        if not cancelled:
            logger.info(
                "暂停请求已持久化，等待任务所属进程释放资源 | task=%s",
                task_id,
            )
        document = await tasks_dao.get_task(
            db,
            project_id=project_id,
            task_id=task_id,
        ) or document

    obs_log(
        "用户请求暂停任务",
        task_id=task_id,
        project_id=project_id,
        source="project_task_control",
        level="notice",
        event="task_pause_requested",
        data={"status": document.get("status")},
    )
    return document


async def _run_resumed_task(document: dict[str, Any]) -> None:
    task_id = str(document.get("task_id") or "")
    project_id = str(document.get("project_id") or "")
    await execute_project_task(
        task_id,
        project_id,
        str(document.get("task_type") or ""),
        build_task_runtime_params(document),
    )
    batch_id = str(document.get("batch_id") or "")
    if batch_id and int(document.get("batch_total") or 0) > 1:
        from api.services.project_task_batch import notify_project_batch_if_complete

        await notify_project_batch_if_complete(
            batch_id=batch_id,
            project_id=project_id,
        )


async def resume_project_task(
    db: Any,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    current = await tasks_dao.get_task(
        db,
        project_id=project_id,
        task_id=task_id,
    )
    if not current:
        raise ProjectTaskNotFoundError("任务不存在")
    if str(current.get("status") or "") != "paused":
        raise ProjectTaskStateError("只有已暂停的任务可以继续")
    task_type = str(current.get("task_type") or "")
    if task_type not in supported_task_types():
        raise ProjectTaskStateError(f"任务类型当前没有可用执行器: {task_type}")

    resumed = await tasks_dao.resume_task(
        db,
        project_id=project_id,
        task_id=task_id,
    )
    if not resumed:
        raise ProjectTaskStateError("任务状态已变化，请刷新后重试")

    spawn_background(
        _run_resumed_task(resumed),
        name=f"task-resume:{task_id}",
    )
    obs_log(
        "用户继续任务",
        task_id=task_id,
        project_id=project_id,
        source="project_task_control",
        level="notice",
        event="task_resumed",
        data={"task_type": task_type},
    )
    logger.notice("任务已从检查点重新排队 | task=%s", task_id)
    return resumed
