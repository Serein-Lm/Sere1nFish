"""Persistent task recovery and stale-progress monitoring."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from api.dao import mobile_collect, mobile_execution_leases, tasks
from api.services.project_task_batch import ProjectTaskJob, run_project_task_batch
from api.services.project_task_runtime import (
    build_task_runtime_params,
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
    return build_task_runtime_params(task)


@dataclass(frozen=True)
class _RecoveryWorkItem:
    name: str
    run: Callable[[], Awaitable[Any]]


async def _run_recovery_work_items(
    items: list[_RecoveryWorkItem],
    *,
    concurrency: int,
) -> None:
    """Resume task groups gradually so startup does not starve the API."""
    limit = max(1, int(concurrency))
    slots = asyncio.Semaphore(limit)

    async def _run_one(item: _RecoveryWorkItem) -> None:
        async with slots:
            try:
                await item.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("恢复任务组执行失败: %s", item.name)

    logger.info("开始分批恢复 %d 个任务组，并发上限=%d", len(items), limit)
    await asyncio.gather(*(_run_one(item) for item in items))


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
    work_items: list[_RecoveryWorkItem] = []
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

        async def _run_batch(
            *,
            recovered_batch_id: str = batch_id,
            recovered_jobs: list[ProjectTaskJob] = jobs,
            recovered_core_concurrency: int = core_concurrency,
            recovered_dispatch_concurrency: int | None = (
                min(
                    len(jobs),
                    max(
                        core_concurrency,
                        int(
                            items[0].get("batch_dispatch_concurrency")
                            or tuning.company_dispatch_concurrency
                        ),
                    ),
                )
                if mobile_aware
                else None
            ),
            recovered_aggregate_notification: bool = aggregate_notification,
        ) -> None:
            await run_project_task_batch(
                batch_id=recovered_batch_id,
                project_id=recovered_jobs[0].project_id,
                jobs=recovered_jobs,
                executor=execute_project_task,
                concurrency=recovered_core_concurrency,
                dispatch_concurrency=recovered_dispatch_concurrency,
                aggregate_notification=recovered_aggregate_notification,
            )

        work_items.append(
            _RecoveryWorkItem(
                name=f"batch:{batch_id}",
                run=_run_batch,
            )
        )
        scheduled += len(jobs)

    for item in singles:
        task_id = str(item.get("task_id") or "")
        project_id = str(item.get("project_id") or "")
        task_type = str(item.get("task_type") or "")
        params = _runtime_params(item)

        async def _run_single(
            *,
            recovered_task_id: str = task_id,
            recovered_project_id: str = project_id,
            recovered_task_type: str = task_type,
            recovered_params: dict[str, Any] = params,
        ) -> None:
            await execute_project_task(
                recovered_task_id,
                recovered_project_id,
                recovered_task_type,
                recovered_params,
            )

        work_items.append(
            _RecoveryWorkItem(
                name=f"task:{task_id}",
                run=_run_single,
            )
        )
        scheduled += 1
    if work_items:
        spawn_background(
            _run_recovery_work_items(
                work_items,
                concurrency=tuning.recovery_group_concurrency,
            ),
            name="task-runtime-recovery",
        )
    return scheduled


async def recover_interrupted_runtime(db: Any) -> dict[str, int]:
    """Recover only stale runtime state; active processes keep their work."""
    mobile_leases = await mobile_execution_leases.cleanup_expired(db)
    active_leases = await mobile_execution_leases.list_active(db)
    active_run_task_ids = {
        str(item.get("task_id") or "")
        for item in active_leases
        if str(item.get("kind") or "") == "mobile_collect"
    }
    mobile_task_defs = await mobile_collect.reset_interrupted_task_defs(
        db,
        active_run_task_ids=active_run_task_ids,
    )
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
