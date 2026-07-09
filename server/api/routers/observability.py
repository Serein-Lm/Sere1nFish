"""观测层统一 API（企业级）。

汇聚三类观测数据为单一看板入口：
- 任务状态（tasks 集合）
- Token / 费用 / 耗时（TokenTracker，进程内环形缓冲，多层级聚合）
- 结构化日志 / 事件（进程内环形缓冲，任意模块经 core.observability.obs_log 接入）

前缀：/api/v1/observability。所有端点需 JWT。
列表查询遵循项目统一范式：POST + 分页（PageRequest/PageResponse）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth import get_current_active_user
from api.db.mongodb import get_db
from api.schemas.pagination import PageRequest, PageResponse
from core.observability import get_obs_logger, obs_log

router = APIRouter(dependencies=[Depends(get_current_active_user)])

TASKS_COLLECTION = "tasks"


# ── 模型 ──

class LogQueryRequest(PageRequest):
    """日志查询（分页 + 过滤）"""
    project_id: str = Field(default="", description="项目 ID 过滤")
    task_id: str = Field(default="", description="任务 ID 过滤")
    source: str = Field(default="", description="来源模块过滤")
    level: str = Field(default="", description="精确级别过滤")
    min_level: str = Field(default="", description="最低级别(debug<info<notice<warning<error)")
    event: str = Field(default="", description="事件类型过滤")
    since: float | None = Field(default=None, description="起始时间戳(unix 秒)")


class LogIngestRequest(BaseModel):
    """通用日志/事件写入（供外部模块/客户端接入观测层）"""
    message: str
    task_id: str = ""
    project_id: str = ""
    source: str = ""
    level: str = "info"
    event: str = ""
    data: dict[str, Any] | None = None
    phase: str = ""
    agent: str = ""


# ── 内部辅助 ──

def _tracker():
    from Sere1nGraph.graph.observability import get_global_tracker

    return get_global_tracker()


async def _task_status_counts(db) -> dict[str, Any]:
    pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    rows = await db[TASKS_COLLECTION].aggregate(pipeline).to_list(50)
    by_status = {r["_id"]: r["count"] for r in rows if r["_id"]}
    return {"total": sum(by_status.values()), "by_status": by_status}


async def _recent_failed_tasks(db, limit: int = 10) -> list[dict]:
    cursor = (
        db[TASKS_COLLECTION]
        .find(
            {"status": "error"},
            {"_id": 0, "task_id": 1, "project_id": 1, "task_type": 1,
             "error": 1, "updated_at": 1},
        )
        .sort("updated_at", -1)
        .limit(limit)
    )
    return await cursor.to_list(limit)


async def _task_counts_by_type(db) -> dict[str, dict[str, Any]]:
    """tasks 集合按场景(task_type)分组 + 状态分布。"""
    pipeline = [
        {"$match": {"task_type": {"$ne": ""}}},
        {"$group": {"_id": {"task_type": "$task_type", "status": "$status"}, "count": {"$sum": 1}}},
    ]
    rows = await db[TASKS_COLLECTION].aggregate(pipeline).to_list(500)
    result: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r.get("_id") or {}
        ttype = key.get("task_type")
        if not ttype:
            continue
        status = key.get("status") or "unknown"
        entry = result.setdefault(ttype, {"total": 0, "by_status": {}})
        entry["total"] += r["count"]
        entry["by_status"][status] = entry["by_status"].get(status, 0) + r["count"]
    return result


# ── 统一总览 ──

@router.get("/overview")
async def observability_overview() -> dict:
    """统一观测总览：Token 全局 + 任务状态分布 + 日志级别分布 + 近期失败/告警。

    看板首屏单一数据源。
    """
    db = get_db()
    tracker = _tracker()
    token_stats, task_status, recent_failed = await asyncio.gather(
        tracker.get_stats_async(),
        _task_status_counts(db),
        _recent_failed_tasks(db, limit=10),
    )
    obs_logger = get_obs_logger()
    log_levels = obs_logger.count_by_level()
    recent_warn = _logs_recent(min_level="warning", limit=10)
    return {
        "token": token_stats,
        "tasks": task_status,
        "logs": {"by_level": log_levels, "recent_warn_error": recent_warn},
        "recent_failed_tasks": recent_failed,
    }


def _logs_recent(*, min_level: str = "", limit: int = 10) -> list[dict]:
    items, _ = get_obs_logger().query_logs(min_level=min_level, limit=limit, skip=0)
    return items


# ── 多层级 Token 统计（统一命名空间下的便捷代理）──

@router.get("/stats")
async def stats(project_id: str = "", task_id: str = "", phase: str = "", agent: str = "", task_type: str = "") -> dict:
    """多层级 Token 聚合统计：不传=全局；可组合 project_id/task_id/phase/agent/task_type。"""
    return await _tracker().get_stats_async(
        project_id=project_id, task_id=task_id, phase=phase, agent=agent, task_type=task_type
    )


@router.get("/scenarios")
async def scenarios() -> dict:
    """按任务场景(task_type)汇总：合并 Token 用量与任务状态分布。

    场景全集 = tasks 集合出现的 task_type ∪ token 记录 by_task_type 的 key。
    每个场景一行：{ task_type, token: {...}, tasks: {total, by_status} }。
    """
    db = get_db()
    tracker = _tracker()
    global_stats, task_counts = await asyncio.gather(
        tracker.get_stats_async(),
        _task_counts_by_type(db),
    )
    token_by_type = global_stats.get("by_task_type", {}) or {}
    scenario_keys = set(task_counts.keys()) | set(token_by_type.keys())

    items = []
    for ttype in sorted(scenario_keys):
        token_stats = await tracker.get_stats_async(task_type=ttype)
        items.append({
            "task_type": ttype,
            "token": token_stats,
            "tasks": task_counts.get(ttype, {"total": 0, "by_status": {}}),
        })
    return {"items": items, "total": len(items)}


@router.get("/hierarchy")
async def hierarchy(project_id: str = "") -> dict:
    """Token 层级树（全局 → 项目 → 任务 → 阶段）。"""
    return await _tracker().get_hierarchy_async(project_id or "")


@router.get("/turns")
async def turns(project_id: str = "", task_id: str = "", limit: int = 100) -> dict:
    """按轮次查看 token 用量；无 turn_id 的记录按单次 LLM run 展示。"""
    safe_limit = min(max(limit, 1), 500)
    items = await _tracker().get_turns_async(project_id=project_id, task_id=task_id, limit=safe_limit)
    return {"items": items, "total": len(items), "limit": safe_limit}


# ── 日志 ──

@router.post("/logs/query")
async def query_logs(body: LogQueryRequest | None = None) -> dict:
    """分页查询观测日志/事件。"""
    if body is None:
        body = LogQueryRequest()
    items, total = get_obs_logger().query_logs(
        project_id=body.project_id,
        task_id=body.task_id,
        source=body.source,
        level=body.level,
        min_level=body.min_level,
        event=body.event,
        since=body.since,
        limit=body.limit,
        skip=body.skip,
    )
    return PageResponse.build(items=items, total=total, page=body.page, page_size=body.page_size)


@router.post("/logs")
async def ingest_log(body: LogIngestRequest) -> dict:
    """通用日志/事件写入接入点（外部模块/客户端可直接 push）。"""
    log_id = obs_log(
        body.message,
        task_id=body.task_id,
        project_id=body.project_id,
        source=body.source or "external",
        level=body.level,
        event=body.event,
        data=body.data,
        phase=body.phase,
        agent=body.agent,
    )
    return {"ok": True, "log_id": log_id}


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, limit: int = 200, min_level: str = "") -> dict:
    """便捷拉取某任务的日志（按时间倒序）。"""
    items, total = get_obs_logger().query_logs(task_id=task_id, min_level=min_level, limit=limit, skip=0)
    return {"task_id": task_id, "items": items, "total": total}
