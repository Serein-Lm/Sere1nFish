"""手机采集任务框架 — API 路由(薄层)。

统一任务类型 mobile_collect: 自定义采集任务定义 + 启停 + 定时调度 + 增量记录查询。
业务流程收敛在 service/runtime/dao, 本层只做鉴权、请求/响应适配与调用。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_active_user
from api.db.mongodb import get_db
from api.dao import mobile_collect as collect_dao
from api.dao import schedules as schedules_dao
from api.models.mobile_collect import (
    CollectTaskDef,
    CollectTaskUpdate,
    RecordsListRequest,
    ScheduleCreate,
    ScheduleUpdate,
)
from core.mobile.collect import request_stop
from core.mobile.collect.presets import PRESETS
from core.mobile.collect.source_links import list_source_link_strategies
from core.background import spawn_background
from core.logger import get_logger

logger = get_logger("mobile_collect_router")

router = APIRouter(dependencies=[Depends(get_current_active_user)])

_TASK_TYPE = "mobile_collect"


class StopRequest(BaseModel):
    run_task_id: str | None = None


# ── 任务定义 CRUD ──────────────────────────────────────

@router.post("/tasks")
async def create_task_def(payload: CollectTaskDef):
    db = get_db()
    doc = await collect_dao.create_task_def(db, payload.model_dump())
    return doc


@router.get("/tasks")
async def list_task_defs(project_id: str | None = None):
    db = get_db()
    items = await collect_dao.list_task_defs(db, project_id=project_id)
    return {"items": items, "total": len(items)}


@router.get("/source-link-strategies")
async def source_link_strategies():
    items = list_source_link_strategies()
    return {
        "items": [
            {
                "strategy": item.strategy,
                "label": item.label,
                "description": item.description,
            }
            for item in items
        ]
    }


@router.get("/tasks/{task_def_id}")
async def get_task_def(task_def_id: str):
    db = get_db()
    doc = await collect_dao.get_task_def(db, task_def_id)
    if not doc:
        raise HTTPException(404, "采集任务定义不存在")
    return doc


@router.patch("/tasks/{task_def_id}")
async def update_task_def(task_def_id: str, payload: CollectTaskUpdate):
    db = get_db()
    existing = await collect_dao.get_task_def(db, task_def_id)
    if not existing:
        raise HTTPException(404, "采集任务定义不存在")
    doc = await collect_dao.update_task_def(db, task_def_id, payload.model_dump(exclude_none=True))
    return doc


@router.delete("/tasks/{task_def_id}")
async def delete_task_def(task_def_id: str):
    db = get_db()
    deleted = await collect_dao.delete_task_def(db, task_def_id)
    if not deleted:
        raise HTTPException(404, "采集任务定义不存在")
    return {"ok": True, "task_def_id": task_def_id}


# ── 启动 / 停止 ────────────────────────────────────────

@router.post("/tasks/{task_def_id}/run")
async def run_task(task_def_id: str):
    """手动启动一次采集(创建统一任务并异步运行)。"""
    db = get_db()
    task_def = await collect_dao.get_task_def(db, task_def_id)
    if not task_def:
        raise HTTPException(404, "采集任务定义不存在")
    if task_def.get("status") == "running":
        raise HTTPException(409, "该采集任务正在运行中")

    from api.routers.project_api import _execute_task, TASKS_COLLECTION

    project_id = task_def.get("project_id") or ""
    params = {"task_def_id": task_def_id}
    task_id = uuid.uuid4().hex[:12]
    await db[TASKS_COLLECTION].insert_one(
        {
            "task_id": task_id,
            "project_id": project_id,
            "task_type": _TASK_TYPE,
            "params": params,
            "status": "pending",
            "progress": {},
            "trigger": "manual",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
    )
    spawn_background(
        _execute_task(task_id, project_id, _TASK_TYPE, params),
        name=f"mobile_collect:{task_id}",
    )
    return {"task_id": task_id, "task_def_id": task_def_id, "status": "pending"}


@router.post("/tasks/{task_def_id}/stop")
async def stop_task(task_def_id: str, payload: StopRequest | None = None):
    """停止运行中的采集任务(协作式取消, 会释放设备)。"""
    db = get_db()
    task_def = await collect_dao.get_task_def(db, task_def_id)
    if not task_def:
        raise HTTPException(404, "采集任务定义不存在")
    run_task_id = (payload.run_task_id if payload else None) or task_def.get("last_run_task_id")
    if not run_task_id:
        raise HTTPException(400, "没有可停止的运行实例")
    ok = request_stop(run_task_id)
    return {"ok": ok, "run_task_id": run_task_id}


# ── 试跑预览(dry-run) ──────────────────────────────────

class DryRunRequest(BaseModel):
    preview_limit: int = 50


@router.post("/tasks/{task_def_id}/dry-run")
async def dry_run_task(task_def_id: str, payload: DryRunRequest | None = None):
    """对已保存的采集任务定义执行一次试跑:导航+截屏+结构化,但不入库、不通知。"""
    from api.services.mobile_collect_pipeline import dry_run_collect

    db = get_db()
    task_def = await collect_dao.get_task_def(db, task_def_id)
    if not task_def:
        raise HTTPException(404, "采集任务定义不存在")
    if task_def.get("status") == "running":
        raise HTTPException(409, "该采集任务正在运行中,无法试跑")

    limit = payload.preview_limit if payload else 50
    run_task_id = "dry-" + uuid.uuid4().hex[:12]
    result = await dry_run_collect(
        run_task_id,
        task_def.get("project_id") or "",
        task_def,
        preview_limit=limit,
    )
    return {"task_def_id": task_def_id, "run_task_id": run_task_id, **result}


@router.post("/dry-run")
async def dry_run_inline(payload: CollectTaskDef, preview_limit: int = 50):
    """对未保存的采集配置执行一次试跑,用于评估预设/自定义配置的采集效果。"""
    from api.services.mobile_collect_pipeline import dry_run_collect

    task_def = payload.model_dump()
    task_def.setdefault("task_def_id", "inline-" + uuid.uuid4().hex[:8])
    run_task_id = "dry-" + uuid.uuid4().hex[:12]
    result = await dry_run_collect(
        run_task_id,
        task_def.get("project_id") or "",
        task_def,
        preview_limit=preview_limit,
    )
    return {"run_task_id": run_task_id, **result}


# ── 采集记录 ────────────────────────────────────────────

@router.post("/records/list")
async def list_records(payload: RecordsListRequest):
    db = get_db()
    items, total = await collect_dao.list_records(
        db,
        task_def_id=payload.task_def_id,
        project_id=payload.project_id,
        only_incremental=payload.only_incremental,
        min_score=payload.min_score,
        skip=payload.skip,
        limit=payload.limit,
    )
    return {"items": items, "total": total, "skip": payload.skip, "limit": payload.limit}


# ── 定时调度 ────────────────────────────────────────────

@router.get("/schedules")
async def list_schedules(target_id: str | None = None):
    db = get_db()
    items = await schedules_dao.list_schedules(db, target_id=target_id)
    return {"items": items, "total": len(items)}


@router.post("/schedules")
async def create_schedule(payload: ScheduleCreate):
    db = get_db()
    task_def = await collect_dao.get_task_def(db, payload.target_id)
    if not task_def:
        raise HTTPException(404, "目标采集任务定义不存在")
    trigger = payload.trigger.model_dump()
    try:
        schedules_dao.validate_trigger(trigger)
    except ValueError as exc:
        raise HTTPException(400, f"触发器无效: {exc}") from exc
    doc = await schedules_dao.create_schedule(
        db,
        name=payload.name,
        target_type=_TASK_TYPE,
        target_id=payload.target_id,
        trigger=trigger,
        enabled=payload.enabled,
    )
    return doc


@router.patch("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, payload: ScheduleUpdate):
    db = get_db()
    existing = await schedules_dao.get_schedule(db, schedule_id)
    if not existing:
        raise HTTPException(404, "调度不存在")
    patch = payload.model_dump(exclude_none=True)
    if "trigger" in patch:
        try:
            schedules_dao.validate_trigger(patch["trigger"])
        except ValueError as exc:
            raise HTTPException(400, f"触发器无效: {exc}") from exc
    doc = await schedules_dao.update_schedule(db, schedule_id, patch)
    return doc


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    db = get_db()
    deleted = await schedules_dao.delete_schedule(db, schedule_id)
    if not deleted:
        raise HTTPException(404, "调度不存在")
    return {"ok": True, "schedule_id": schedule_id}


# ── 预设模板 ────────────────────────────────────────────

@router.get("/presets")
async def list_presets():
    return {"items": PRESETS}
