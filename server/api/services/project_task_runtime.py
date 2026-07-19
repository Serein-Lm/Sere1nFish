"""Persistent project-task execution and dispatcher registry."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Mapping
from typing import Any, Callable

from api.dao import tasks as tasks_dao
from api.db.mongodb import get_db
from core.logger import get_logger
from core.observability import obs_log


TaskDispatcher = Callable[[str, str, dict[str, Any]], Awaitable[Any]]

logger = get_logger("project_task_runtime")
_RUNTIME_ID = uuid.uuid4().hex
_TASK_DISPATCHERS: dict[str, TaskDispatcher] = {}
_HEARTBEAT_INTERVAL_SECONDS = 30.0


def register_task_dispatchers(dispatchers: Mapping[str, TaskDispatcher]) -> None:
    """Register task adapters while keeping execution semantics in this service."""
    _TASK_DISPATCHERS.update(dispatchers)


def supported_task_types() -> frozenset[str]:
    return frozenset(_TASK_DISPATCHERS)


async def _heartbeat(task_id: str) -> None:
    db = get_db()
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        updated = await tasks_dao.heartbeat_task(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
        )
        if not updated:
            return


async def execute_project_task(
    task_id: str,
    project_id: str,
    task_type: str,
    params: dict[str, Any],
) -> Any:
    """Atomically claim and execute one persistent task."""
    dispatcher = _TASK_DISPATCHERS.get(task_type)
    if dispatcher is None:
        raise ValueError(f"不支持的 task_type: {task_type}")

    db = get_db()
    claimed = await tasks_dao.claim_task(
        db,
        task_id=task_id,
        runtime_id=_RUNTIME_ID,
    )
    if not claimed:
        logger.info("任务认领跳过 | task=%s status 已变化", task_id)
        return None

    from Sere1nGraph.graph.observability import get_global_tracker

    tracker = get_global_tracker()
    tracker.push_context(
        project_id=project_id,
        task_id=task_id,
        turn_id=task_id,
        task_type=task_type,
    )
    heartbeat_task = asyncio.create_task(
        _heartbeat(task_id),
        name=f"task-heartbeat:{task_id}",
    )
    started = time.monotonic()
    logger.notice(
        "任务启动 | task=%s type=%s project=%s attempt=%s",
        task_id,
        task_type,
        project_id,
        claimed.get("attempt_count", 1),
    )
    obs_log(
        "任务启动",
        task_id=task_id,
        project_id=project_id,
        source="task_runner",
        level="notice",
        event="task_start",
        data={
            "task_type": task_type,
            "attempt_count": claimed.get("attempt_count", 1),
            "recovery_count": claimed.get("recovery_count", 0),
        },
    )

    try:
        result = await dispatcher(task_id, project_id, params)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        await tasks_dao.complete_task(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
            elapsed_ms=elapsed_ms,
            result=result,
        )
        obs_log(
            f"任务完成 ({elapsed_ms / 1000:.1f}s)",
            task_id=task_id,
            project_id=project_id,
            source="task_runner",
            level="notice",
            event="task_done",
            data={"task_type": task_type, "elapsed_ms": elapsed_ms},
        )
        logger.notice("任务完成 | task=%s (%.1fs)", task_id, elapsed_ms / 1000)
        return result
    except asyncio.CancelledError:
        await tasks_dao.release_interrupted_task(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
            reason="服务进程关闭，任务等待新进程恢复",
        )
        logger.warning("任务执行被进程关闭中断，已退回待恢复 | task=%s", task_id)
        raise
    except Exception as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        await tasks_dao.fail_task(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
            elapsed_ms=elapsed_ms,
            error=str(exc),
        )
        obs_log(
            f"任务失败: {exc}",
            task_id=task_id,
            project_id=project_id,
            source="task_runner",
            level="error",
            event="task_error",
            data={
                "task_type": task_type,
                "error": str(exc),
                "elapsed_ms": elapsed_ms,
            },
        )
        logger.error("任务失败 | task=%s: %s", task_id, exc)
        return None
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        tracker.pop_context()
