"""手机采集任务 — 服务层分派器。

作为统一任务系统的一个 task_type=mobile_collect 的 dispatcher:
从 params 取 task_def_id, 加载任务定义, 交由运行时 Pipeline 执行,
并维护任务定义的运行状态(idle/running)。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from api.db.mongodb import get_db
from api.dao import mobile_collect as collect_dao
from core.logger import get_logger
from core.mobile.collect import run_collect_task
from core.mobile.execution_queue import (
    PriorityExecutionQueue as _PriorityTaskDefinitionQueue,
    queue_priority_value as _queue_priority_value,
)

logger = get_logger("mobile_collect_service")

_DEVICE_READY_TIMEOUT_SECONDS: float | None = None
_DEVICE_READY_POLL_SECONDS = 2.0


_TASK_DEFINITION_QUEUE_LOCKS: dict[str, _PriorityTaskDefinitionQueue] = {}


def _task_definition_queue_lock(
    task_def_id: str,
) -> _PriorityTaskDefinitionQueue:
    lock = _TASK_DEFINITION_QUEUE_LOCKS.get(task_def_id)
    if lock is None:
        lock = _PriorityTaskDefinitionQueue()
        _TASK_DEFINITION_QUEUE_LOCKS[task_def_id] = lock
    return lock


async def wait_for_mobile_device_ready(
    device_id: str,
    *,
    timeout_seconds: float | None = _DEVICE_READY_TIMEOUT_SECONDS,
    poll_seconds: float = _DEVICE_READY_POLL_SECONDS,
) -> str:
    """Wait for a stable device identity without failing background work on disconnect."""
    normalized = str(device_id or "").strip()
    if not normalized:
        raise ValueError("手机采集任务缺少执行设备")

    from core.mobile.manager import MobileDeviceManager

    manager = MobileDeviceManager()
    manager.start_polling()
    loop = asyncio.get_running_loop()
    deadline = (
        None
        if timeout_seconds is None
        else loop.time() + max(0.1, float(timeout_seconds))
    )
    waiting_logged = False
    while True:
        endpoint = await asyncio.to_thread(
            manager.resolve_ready_adb_device_id,
            normalized,
        )
        if endpoint:
            return endpoint
        if not waiting_logged:
            logger.warning(
                "手机设备离线，采集任务保持排队等待 | device=%s",
                normalized,
            )
            waiting_logged = True
        sleep_seconds = max(0.0, float(poll_seconds))
        if deadline is not None:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    f"手机设备未就绪: {normalized}，等待 "
                    f"{timeout_seconds:.0f}s 后仍无在线 ADB 端点"
                )
            sleep_seconds = min(sleep_seconds, remaining)
        await asyncio.sleep(sleep_seconds)


async def run_mobile_collect_definition(
    db,
    *,
    run_task_id: str,
    project_id: str,
    task_def_id: str,
    runtime_overrides: dict | None = None,
    requested_by: str = "",
    queue_priority: str = "normal",
    on_started: Callable[[], Awaitable[None]] | None = None,
    on_waiting: Callable[[str, str], Awaitable[None]] | None = None,
) -> dict:
    """原子占用并执行一个数据库任务定义，允许编排层注入本轮目标上下文。"""
    if not task_def_id:
        raise ValueError("缺少 task_def_id")

    queue_lock = _task_definition_queue_lock(task_def_id)
    priority_value = _queue_priority_value(queue_priority)
    if queue_lock.locked():
        if on_waiting is not None:
            await on_waiting("waiting_mobile", "等待同一采集定义的上一轮任务结束")
        logger.info(
            "手机采集定义进入等待队列 def=%s run=%s priority=%s",
            task_def_id,
            run_task_id,
            queue_priority,
        )
    async with queue_lock.slot(priority_value):
        return await _run_mobile_collect_definition_claimed(
            db,
            run_task_id=run_task_id,
            project_id=project_id,
            task_def_id=task_def_id,
            runtime_overrides=runtime_overrides,
            requested_by=requested_by,
            queue_priority=queue_priority,
            on_started=on_started,
            on_waiting=on_waiting,
        )


async def _run_mobile_collect_definition_claimed(
    db,
    *,
    run_task_id: str,
    project_id: str,
    task_def_id: str,
    runtime_overrides: dict | None = None,
    requested_by: str = "",
    queue_priority: str = "normal",
    on_started: Callable[[], Awaitable[None]] | None = None,
    on_waiting: Callable[[str, str], Awaitable[None]] | None = None,
) -> dict:
    """Claim and run one definition after its in-process queue slot is acquired."""

    task_def = await collect_dao.get_task_def(db, task_def_id)
    if not task_def:
        raise ValueError(f"采集任务定义不存在: {task_def_id}")

    requested_task_def = {**task_def, **(runtime_overrides or {})}
    device_id = str(requested_task_def.get("device_id") or "").strip()
    if on_waiting is not None:
        await on_waiting("waiting_mobile", f"等待手机 {device_id} 上线并就绪")
    ready_endpoint = await wait_for_mobile_device_ready(device_id)
    logger.info(
        "手机采集设备已就绪 | def=%s run=%s device=%s adb=%s",
        task_def_id,
        run_task_id,
        device_id,
        ready_endpoint,
    )

    claim_operation = asyncio.create_task(
        collect_dao.claim_task_run(
            db,
            task_def_id,
            run_task_id=run_task_id,
        ),
        name=f"mobile-collect-claim:{run_task_id}",
    )
    try:
        claimed = await asyncio.shield(claim_operation)
    except asyncio.CancelledError:
        # MongoDB may have committed the claim even when the caller is cancelled
        # before Motor delivers the result. Finish the atomic operation and only
        # release the run that this coroutine actually owns.
        claimed = await asyncio.shield(claim_operation)
        if claimed:
            await asyncio.shield(
                collect_dao.set_task_status(
                    db,
                    task_def_id,
                    "idle",
                    expected_run_task_id=run_task_id,
                )
            )
        raise
    if not claimed:
        raise RuntimeError(f"采集任务正在运行中: {task_def_id}")

    effective_task_def = {**claimed, **(runtime_overrides or {})}
    effective_task_def["task_def_id"] = task_def_id
    try:
        if on_waiting is not None:
            await on_waiting("waiting_mobile", f"设备已在线，等待手机 {device_id} 的执行租约")
        from api.services.mobile_device_leases import background_device_lease

        async with background_device_lease(
            db,
            device_id=str(effective_task_def.get("device_id") or ""),
            run_task_id=run_task_id,
            requested_by=requested_by,
            queue_priority=queue_priority,
        ):
            if on_started is not None:
                await on_started()
            result = await run_collect_task(
                db,
                run_task_id=run_task_id,
                project_id=project_id or effective_task_def.get("project_id"),
                task_def=effective_task_def,
            )
        logger.notice(
            f"采集任务完成 | def={task_def_id} run={run_task_id} "
            f"total={result['total']} new={result['new']} changed={result['changed']}"
        )
        return result
    finally:
        await collect_dao.set_task_status(
            db,
            task_def_id,
            "idle",
            expected_run_task_id=run_task_id,
        )


async def _dispatch_mobile_collect(task_id: str, project_id: str, params: dict) -> dict:
    """统一任务分派入口(签名对齐 TASK_DISPATCHERS)。"""
    from api.services.task_progress import update_source_progress, update_task_stage

    db = get_db()

    async def on_waiting(stage: str, message: str) -> None:
        await update_task_stage(db, task_id=task_id, stage=stage, message=message)
        await update_source_progress(
            db, task_id=task_id, source="mobile_collect", status="waiting", message=message,
        )

    async def on_started() -> None:
        await update_task_stage(db, task_id=task_id, stage="mobile_collect", message="手机采集已开始")
        await update_source_progress(
            db, task_id=task_id, source="mobile_collect", status="running", message="正在手机中搜索并读取内容",
        )

    return await run_mobile_collect_definition(
        db,
        run_task_id=task_id,
        project_id=project_id,
        task_def_id=params.get("task_def_id", ""),
        requested_by=str(params.get("_requested_by") or ""),
        queue_priority=str(params.get("queue_priority") or "normal"),
        runtime_overrides={
            "parent_task_id": task_id,
            "progress_source": "mobile_collect",
            "progress_label": "手机采集",
        },
        on_waiting=on_waiting,
        on_started=on_started,
    )


async def dry_run_collect(
    run_task_id: str,
    project_id: str,
    task_def: dict,
    *,
    preview_limit: int = 50,
    requested_by: str = "",
) -> dict:
    """试跑预览:同步执行一次采集但不入库、不发通知,返回结构化预览。

    仍会占用设备、导航、截屏并做视觉结构化,用于评估采集效果;
    不修改任务定义的运行状态(idle/running),避免与真实运行互相干扰。
    """
    db = get_db()
    from api.services.mobile_device_leases import background_device_lease

    async with background_device_lease(
        db,
        device_id=str(task_def.get("device_id") or ""),
        run_task_id=run_task_id,
        requested_by=requested_by,
    ):
        return await run_collect_task(
            db,
            run_task_id=run_task_id,
            project_id=project_id or task_def.get("project_id"),
            task_def=task_def,
            dry_run=True,
            preview_limit=preview_limit,
        )
