"""手机采集任务 — 服务层分派器。

作为统一任务系统的一个 task_type=mobile_collect 的 dispatcher:
从 params 取 task_def_id, 加载任务定义, 交由运行时 Pipeline 执行,
并维护任务定义的运行状态(idle/running)。
"""
from __future__ import annotations

from api.db.mongodb import get_db
from api.dao import mobile_collect as collect_dao
from core.logger import get_logger
from core.mobile.collect import run_collect_task

logger = get_logger("mobile_collect_service")


async def run_mobile_collect_definition(
    db,
    *,
    run_task_id: str,
    project_id: str,
    task_def_id: str,
    runtime_overrides: dict | None = None,
    requested_by: str = "",
) -> dict:
    """原子占用并执行一个数据库任务定义，允许编排层注入本轮目标上下文。"""
    if not task_def_id:
        raise ValueError("缺少 task_def_id")

    task_def = await collect_dao.get_task_def(db, task_def_id)
    if not task_def:
        raise ValueError(f"采集任务定义不存在: {task_def_id}")

    claimed = await collect_dao.claim_task_run(
        db,
        task_def_id,
        run_task_id=run_task_id,
    )
    if not claimed:
        raise RuntimeError(f"采集任务正在运行中: {task_def_id}")

    effective_task_def = {**claimed, **(runtime_overrides or {})}
    effective_task_def["task_def_id"] = task_def_id
    try:
        from api.services.mobile_device_leases import background_device_lease

        async with background_device_lease(
            db,
            device_id=str(effective_task_def.get("device_id") or ""),
            run_task_id=run_task_id,
            requested_by=requested_by,
        ):
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
        await collect_dao.set_task_status(db, task_def_id, "idle")


async def _dispatch_mobile_collect(task_id: str, project_id: str, params: dict) -> dict:
    """统一任务分派入口(签名对齐 TASK_DISPATCHERS)。"""
    return await run_mobile_collect_definition(
        get_db(),
        run_task_id=task_id,
        project_id=project_id,
        task_def_id=params.get("task_def_id", ""),
        requested_by=str(params.get("_requested_by") or ""),
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
