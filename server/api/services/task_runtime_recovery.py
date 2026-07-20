"""Persistent task recovery and stale-progress monitoring."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from api.dao import device_reservations, mobile_collect, tasks
from api.services.project_task_batch import ProjectTaskJob, run_project_task_batch
from api.services.project_task_runtime import (
    execute_project_task,
    supported_task_types,
)
from core.background import spawn_background
from core.logger import get_logger


logger = get_logger("task_runtime_recovery")
_STALL_CHECK_INTERVAL_SECONDS = 60
_STALL_ALERT_AFTER_SECONDS = 30 * 60
_STALL_ALERT_COOLDOWN_SECONDS = 2 * 60 * 60
_RUNTIME_HEARTBEAT_STALE_SECONDS = 2 * 60
_STALL_ALERT_VISIBLE_TASKS = 8


def build_stalled_task_notification(
    stalled: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Build one bounded alert and distinguish dead runtimes from slow work."""
    current = now or datetime.now(timezone.utc)
    heartbeat_before = current - timedelta(
        seconds=_RUNTIME_HEARTBEAT_STALE_SECONDS
    )

    def _heartbeat_stale(item: dict[str, Any]) -> bool:
        heartbeat = item.get("heartbeat_at")
        if not isinstance(heartbeat, datetime):
            return True
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
        return heartbeat < heartbeat_before

    runtime_stale = [item for item in stalled if _heartbeat_stale(item)]
    lines = []
    for item in stalled[:_STALL_ALERT_VISIBLE_TASKS]:
        company = (
            item.get("params", {}).get("company_name")
            or item.get("task_type")
            or "未知目标"
        )
        stage = item.get("progress", {}).get("stage") or "unknown"
        lines.append(f"- {company}，阶段：{stage}")
    hidden = max(0, len(stalled) - len(lines))
    if hidden:
        lines.append(f"- 其余 {hidden} 个目标已合并，不逐条展开")

    if runtime_stale:
        conclusion = f"{len(runtime_stale)} 个运行实例心跳异常，需要优先检查。"
        level = "critical"
    else:
        conclusion = (
            f"{len(stalled)} 个任务心跳正常，但业务进度超过 30 分钟未更新。"
        )
        level = "warning"
    content = "\n".join(
        [
            "**结论**",
            f"- {conclusion}",
            "",
            "**目标摘要**",
            *lines,
        ]
    )
    return level, content, {
        "count": len(stalled),
        "runtime_stale": len(runtime_stale),
        "heartbeat_alive": len(stalled) - len(runtime_stale),
    }


def _runtime_params(task: dict[str, Any]) -> dict[str, Any]:
    params = {
        **dict(task.get("params") or {}),
        "_requested_by": str(task.get("requested_by") or ""),
    }
    batch_id = str(task.get("batch_id") or "")
    batch_total = max(0, int(task.get("batch_total") or 0))
    if batch_id and batch_total > 1:
        params.update({"_batch_id": batch_id, "_batch_total": batch_total})
    return params


async def _schedule_recovered_tasks(recovered: list[dict[str, Any]]) -> int:
    from api.services.info_collection.tuning import get_collection_runtime_tuning

    tuning = await get_collection_runtime_tuning()
    batches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    singles: list[dict[str, Any]] = []
    for task in recovered:
        batch_id = str(task.get("batch_id") or "")
        if batch_id:
            batches[batch_id].append(task)
        else:
            singles.append(task)

    scheduled = 0
    for batch_id, items in batches.items():
        items.sort(key=lambda item: int(item.get("batch_index") or 0))
        jobs = [
            ProjectTaskJob(
                task_id=str(item.get("task_id") or ""),
                project_id=str(item.get("project_id") or ""),
                task_type=str(item.get("task_type") or ""),
                params=_runtime_params(item),
            )
            for item in items
        ]
        core_concurrency = max(
            1,
            int(items[0].get("batch_concurrency") or tuning.company_scan_concurrency),
        )
        mobile_aware = all(job.task_type == "company_scan" for job in jobs)
        aggregate_notification = bool(
            mobile_aware and int(items[0].get("batch_total") or 0) > 1
        )
        spawn_background(
            run_project_task_batch(
                batch_id=batch_id,
                project_id=jobs[0].project_id,
                jobs=jobs,
                executor=execute_project_task,
                concurrency=core_concurrency,
                dispatch_concurrency=len(jobs) if mobile_aware else None,
                aggregate_notification=aggregate_notification,
            ),
            name=f"task-batch-recovery:{batch_id}",
        )
        scheduled += len(jobs)

    for item in singles:
        task_id = str(item.get("task_id") or "")
        spawn_background(
            execute_project_task(
                task_id,
                str(item.get("project_id") or ""),
                str(item.get("task_type") or ""),
                _runtime_params(item),
            ),
            name=f"task-recovery:{task_id}",
        )
        scheduled += 1
    return scheduled


async def recover_interrupted_runtime(db: Any) -> dict[str, int]:
    """Release process-local leases and requeue unfinished persistent tasks."""
    mobile_task_defs = await mobile_collect.reset_interrupted_task_defs(db)
    mobile_leases = await device_reservations.delete_background_reservations(db)
    recovered, exhausted = await tasks.prepare_interrupted_tasks(db)

    supported = supported_task_types()
    resumable = [item for item in recovered if item.get("task_type") in supported]
    unsupported = [item for item in recovered if item.get("task_type") not in supported]
    unsupported_count = await tasks.mark_tasks_unrecoverable(
        db,
        [str(item.get("task_id") or "") for item in unsupported],
        reason="任务类型当前没有可用执行器，无法自动恢复",
    )
    scheduled = await _schedule_recovered_tasks(resumable)

    if exhausted or unsupported_count:
        from api.services.notifications import notify_event_background

        notify_event_background(
            event="task.runtime.recovered",
            title="扫描任务恢复存在异常",
            content=(
                f"达到恢复上限 {exhausted} 条；不支持恢复 {unsupported_count} 条。"
            ),
            level="warning",
            source="task_runtime_recovery",
            context={
                "scheduled": scheduled,
                "exhausted": exhausted,
                "unsupported": unsupported_count,
            },
        )

    return {
        "tasks": len(recovered) + exhausted,
        "resumed_tasks": scheduled,
        "exhausted_tasks": exhausted,
        "unsupported_tasks": unsupported_count,
        "mobile_task_defs": mobile_task_defs,
        "mobile_leases": mobile_leases,
    }


class TaskRuntimeMonitor:
    """Notify on tasks whose domain progress stopped changing."""

    _instance: "TaskRuntimeMonitor | None" = None

    def __init__(self) -> None:
        self._task: asyncio.Task[Any] | None = None

    @classmethod
    def get_instance(cls) -> "TaskRuntimeMonitor":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = spawn_background(
            self._run(),
            name="task-runtime-monitor",
        )

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        from api.db.mongodb import get_db

        while True:
            try:
                await asyncio.sleep(_STALL_CHECK_INTERVAL_SECONDS)
                stalled = await tasks.claim_stalled_task_alerts(
                    get_db(),
                    stale_after_seconds=_STALL_ALERT_AFTER_SECONDS,
                    alert_cooldown_seconds=_STALL_ALERT_COOLDOWN_SECONDS,
                )
                if not stalled:
                    continue
                from api.services.notifications import notify_event

                level, content, context = build_stalled_task_notification(stalled)
                await notify_event(
                    event="task.runtime.stalled",
                    title="扫描任务长时间没有进度",
                    content=content,
                    level=level,
                    source="task_runtime_monitor",
                    context=context,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务异常监控检查失败: %s", exc)
