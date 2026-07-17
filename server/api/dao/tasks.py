"""Project task read access used by API and AI data adapters."""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import TASKS_COLLECTION


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
