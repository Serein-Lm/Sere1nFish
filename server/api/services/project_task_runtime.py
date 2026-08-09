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
_RUNNING_TASKS: dict[str, asyncio.Task[Any]] = {}
_HEARTBEAT_INTERVAL_SECONDS = 30.0
_HEARTBEAT_FAILURE_TIMEOUT_SECONDS = 60.0
_CONTROL_POLL_INTERVAL_SECONDS = 1.0
_NOTIFIED_LLM_CAPACITY_INCIDENTS: set[int] = set()


def register_task_dispatchers(dispatchers: Mapping[str, TaskDispatcher]) -> None:
    """Register task adapters while keeping execution semantics in this service."""
    _TASK_DISPATCHERS.update(dispatchers)


def supported_task_types() -> frozenset[str]:
    return frozenset(_TASK_DISPATCHERS)


def build_task_runtime_params(task: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild internal execution metadata from one persistent task document."""
    params = {
        **dict(task.get("params") or {}),
        "_requested_by": str(task.get("requested_by") or ""),
    }
    batch_id = str(task.get("batch_id") or "")
    batch_total = max(0, int(task.get("batch_total") or 0))
    if batch_id and batch_total > 1:
        params.update({"_batch_id": batch_id, "_batch_total": batch_total})
    return params


async def cancel_running_project_task(
    task_id: str,
    *,
    wait_timeout: float = 1.5,
) -> bool:
    """Cancel one execution child without cancelling its batch worker."""
    running = _RUNNING_TASKS.get(task_id)
    if running is None or running.done():
        return False
    running.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(running), timeout=wait_timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    except Exception:
        logger.exception("暂停任务时执行协程清理异常 | task=%s", task_id)
    return True


async def _heartbeat(task_id: str, owner_task: asyncio.Task[Any]) -> None:
    db = get_db()
    last_confirmed = time.monotonic()
    failures = 0
    while not owner_task.done():
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        try:
            updated = await tasks_dao.heartbeat_task(
                db,
                task_id=task_id,
                runtime_id=_RUNTIME_ID,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            failures += 1
            logger.warning(
                "任务心跳暂时失败 | task=%s failures=%s error=%s",
                task_id,
                failures,
                exc,
            )
            if (
                time.monotonic() - last_confirmed
                >= _HEARTBEAT_FAILURE_TIMEOUT_SECONDS
            ):
                logger.error("任务心跳持续失败，停止执行 | task=%s", task_id)
                owner_task.cancel()
                return
            continue
        if not updated:
            owner_task.cancel()
            return
        failures = 0
        last_confirmed = time.monotonic()


async def _watch_persistent_control(
    task_id: str,
    owner_task: asyncio.Task[Any],
) -> None:
    """Deliver pause intent to the process that actually owns the task."""
    db = get_db()
    while not owner_task.done():
        await asyncio.sleep(_CONTROL_POLL_INTERVAL_SECONDS)
        try:
            if await tasks_dao.is_pause_requested(
                db,
                task_id=task_id,
                runtime_id=_RUNTIME_ID,
            ):
                owner_task.cancel()
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("任务控制状态读取失败 | task=%s error=%s", task_id, exc)


async def execute_project_task(
    task_id: str,
    project_id: str,
    task_type: str,
    params: dict[str, Any],
) -> Any:
    """Run each persistent task in an independently cancellable child task."""
    existing = _RUNNING_TASKS.get(task_id)
    if existing is not None and not existing.done():
        logger.info("任务执行已存在，复用当前实例 | task=%s", task_id)
        try:
            return await asyncio.shield(existing)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling() == 0:
                return None
            raise

    running = asyncio.create_task(
        _execute_project_task(task_id, project_id, task_type, params),
        name=f"project-task:{task_id}",
    )
    _RUNNING_TASKS[task_id] = running
    try:
        try:
            return await running
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling() == 0:
                return None
            raise
    finally:
        if _RUNNING_TASKS.get(task_id) is running:
            _RUNNING_TASKS.pop(task_id, None)


async def _execute_project_task(
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
    owner_task = asyncio.current_task()
    assert owner_task is not None
    heartbeat_task = asyncio.create_task(
        _heartbeat(task_id, owner_task),
        name=f"task-heartbeat:{task_id}",
    )
    control_task = asyncio.create_task(
        _watch_persistent_control(task_id, owner_task),
        name=f"task-control:{task_id}",
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
        while True:
            try:
                result = await dispatcher(task_id, project_id, params)
                break
            except Exception as exc:
                from core.llm_capacity import (
                    LLMCapacityUnavailableError,
                    get_global_llm_capacity_guard,
                )

                if not isinstance(exc, LLMCapacityUnavailableError):
                    raise
                await tasks_dao.mark_task_waiting_resource(
                    db,
                    task_id=task_id,
                    runtime_id=_RUNTIME_ID,
                    stage="waiting_model",
                    message=(
                        "模型额度暂不可用，已保留扫描检查点，"
                        f"约 {exc.retry_after_seconds:.0f} 秒后自动重试"
                    ),
                )
                if exc.incident_id not in _NOTIFIED_LLM_CAPACITY_INCIDENTS:
                    _NOTIFIED_LLM_CAPACITY_INCIDENTS.add(exc.incident_id)
                    from api.services.notifications import notify_event_background

                    notify_event_background(
                        event="llm.capacity.paused",
                        title="模型额度不足，扫描已自动等待",
                        content=(
                            "**结论**\n"
                            "- 未完成任务和 URL 检查点均已保留，不会记为扫描失败。\n\n"
                            "**处理**\n"
                            f"- 系统将在约 {exc.retry_after_seconds:.0f} 秒后单路探测恢复。"
                        ),
                        level="critical",
                        source="project_task_runtime",
                        project_id=project_id,
                        task_id=task_id,
                        dedupe_key="llm-capacity",
                        cooldown_seconds=1800,
                    )
                logger.warning(
                    "任务等待模型容量恢复 | task=%s incident=%s retry_after=%.0fs",
                    task_id,
                    exc.incident_id,
                    exc.retry_after_seconds,
                )
                await get_global_llm_capacity_guard().wait_for_retry_window(
                    exc.incident_id
                )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        completed = await tasks_dao.complete_task(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
            elapsed_ms=elapsed_ms,
            result=result,
        )
        if not completed and await tasks_dao.is_pause_requested(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
        ):
            await tasks_dao.mark_task_paused(
                db,
                task_id=task_id,
                runtime_id=_RUNTIME_ID,
            )
            logger.info("任务在完成提交前收到暂停请求 | task=%s", task_id)
            return None
        if not completed:
            current = await tasks_dao.get_task(
                db,
                project_id=project_id,
                task_id=task_id,
            )
            logger.warning(
                "任务完成提交已被较新状态取代 | task=%s status=%s runtime=%s",
                task_id,
                (current or {}).get("status"),
                (current or {}).get("runtime_id"),
            )
            return result
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
        if await tasks_dao.is_pause_requested(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
        ):
            await tasks_dao.mark_task_paused(
                db,
                task_id=task_id,
                runtime_id=_RUNTIME_ID,
            )
            obs_log(
                "任务已暂停",
                task_id=task_id,
                project_id=project_id,
                source="task_runner",
                level="notice",
                event="task_paused",
                data={"task_type": task_type},
            )
            logger.notice("任务已按用户请求暂停 | task=%s", task_id)
            return None
        await tasks_dao.release_interrupted_task(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
            reason="服务进程关闭，任务等待新进程恢复",
        )
        logger.warning("任务执行被进程关闭中断，已退回待恢复 | task=%s", task_id)
        raise
    except Exception as exc:
        if await tasks_dao.is_pause_requested(
            db,
            task_id=task_id,
            runtime_id=_RUNTIME_ID,
        ):
            await tasks_dao.mark_task_paused(
                db,
                task_id=task_id,
                runtime_id=_RUNTIME_ID,
            )
            logger.info("任务异常清理期间完成暂停 | task=%s", task_id)
            return None
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
        control_task.cancel()
        await asyncio.gather(
            heartbeat_task,
            control_task,
            return_exceptions=True,
        )
        tracker.pop_context()
