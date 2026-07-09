"""历史观测日志 DAO（兼容旧 task_logs 集合）。

当前运行时日志只存在于 `core.observability.ObservabilityLogger` 的进程内环形缓冲，
不再写入 MongoDB。本 DAO 仅保留给旧集合查询/清理脚本或兼容删除流程使用。
"""

from __future__ import annotations

import os
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from core.observability import LEVELS, TASK_LOGS_COLLECTION


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[TASK_LOGS_COLLECTION]
    await coll.create_index("task_id")
    await coll.create_index("project_id")
    await coll.create_index([("task_id", 1), ("ts", 1)])
    await coll.create_index([("project_id", 1), ("ts", -1)])
    await coll.create_index("level")
    await coll.create_index("source")
    await coll.create_index("ts")
    # TTL 索引：按 created_at 自动过期，控制 DB 容量（默认 14 天，OBS_LOG_TTL_DAYS 可调）
    ttl_days = int(os.getenv("OBS_LOG_TTL_DAYS", "14") or 14)
    try:
        await coll.create_index("created_at", expireAfterSeconds=ttl_days * 86400)
    except Exception:
        # 已存在不同 expireAfterSeconds 的 TTL 索引时忽略（可用 collMod 在线调整）
        pass


async def query_logs(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str = "",
    task_id: str = "",
    source: str = "",
    level: str = "",
    min_level: str = "",
    event: str = "",
    since: float | None = None,
    limit: int = 100,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """分页查询日志，返回 (items, total)。按 ts 倒序。

    level 精确匹配；min_level 取该级别及以上（debug<info<notice<warning<error）。
    """
    q: dict[str, Any] = {}
    if project_id:
        q["project_id"] = project_id
    if task_id:
        q["task_id"] = task_id
    if source:
        q["source"] = source
    if event:
        q["event"] = event
    if level:
        q["level"] = level
    elif min_level and min_level in LEVELS:
        allowed = list(LEVELS[LEVELS.index(min_level):])
        q["level"] = {"$in": allowed}
    if since is not None:
        q["ts"] = {"$gte": since}

    coll = db[TASK_LOGS_COLLECTION]
    total = await coll.count_documents(q)
    cursor = coll.find(q, {"_id": 0}).sort("ts", -1).skip(skip).limit(limit)
    items = await cursor.to_list(limit)
    return items, total


async def count_by_level(
    db: AsyncIOMotorDatabase, *, project_id: str = "", task_id: str = ""
) -> dict[str, int]:
    match: dict[str, Any] = {}
    if project_id:
        match["project_id"] = project_id
    if task_id:
        match["task_id"] = task_id
    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {"$group": {"_id": "$level", "count": {"$sum": 1}}},
    ]
    rows = await db[TASK_LOGS_COLLECTION].aggregate(pipeline).to_list(20)
    return {r["_id"]: r["count"] for r in rows if r["_id"]}


async def delete_logs_by_task(db: AsyncIOMotorDatabase, task_id: str) -> int:
    r = await db[TASK_LOGS_COLLECTION].delete_many({"task_id": task_id})
    return r.deleted_count


async def delete_logs_by_tasks(db: AsyncIOMotorDatabase, task_ids: list[str]) -> int:
    if not task_ids:
        return 0
    r = await db[TASK_LOGS_COLLECTION].delete_many({"task_id": {"$in": task_ids}})
    return r.deleted_count


async def delete_logs_by_project(db: AsyncIOMotorDatabase, project_id: str) -> int:
    r = await db[TASK_LOGS_COLLECTION].delete_many({"project_id": project_id})
    return r.deleted_count
