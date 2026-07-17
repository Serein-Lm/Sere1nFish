"""Project task read access used by API and AI data adapters."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import TASKS_COLLECTION


async def insert_tasks(
    db: AsyncIOMotorDatabase,
    documents: list[dict[str, Any]],
) -> int:
    """Insert project task documents as one atomic batch request."""
    if not documents:
        return 0
    result = await db[TASKS_COLLECTION].insert_many(documents)
    return len(result.inserted_ids)


async def mark_interrupted_tasks(db: AsyncIOMotorDatabase) -> int:
    """进程启动时终结无法跨进程恢复的任务实例。"""
    now = datetime.now(timezone.utc)
    reason = "服务进程已重启，上一进程中的后台任务已中断，请重新下发"
    result = await db[TASKS_COLLECTION].update_many(
        {"status": {"$in": ["pending", "running"]}},
        {
            "$set": {
                "status": "error",
                "error": reason,
                "progress": {"stage": "error", "message": reason},
                "updated_at": now,
                "completed_at": now,
            }
        },
    )
    return int(result.modified_count)


async def list_tasks(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    task_type: str = "",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {"project_id": project_id}
    if task_type:
        query["task_type"] = task_type
    bounded_limit = max(1, min(int(limit or 50), 200))
    total = await db[TASKS_COLLECTION].count_documents(query)
    cursor = (
        db[TASKS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(max(0, int(skip or 0)))
        .limit(bounded_limit)
    )
    return await cursor.to_list(bounded_limit), total
