from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import WEB_TAGS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def insert_web_tagging_result(
    db: AsyncIOMotorDatabase,
    project_id: str,
    url: str,
    data: dict[str, Any],
    task_id: str = "",
    source: str = "web_tagging",
    target_id: str = "",
) -> dict[str, Any]:
    """
    存储 web tagging 扫描结果。

    自动给 data.findings 中每个 finding 注入 finding_id（如果没有的话），
    同时注入 task_id 和 project_id，确保前端能通过 finding_id 关联话术。
    """
    try:
        pid = ObjectId(project_id)
    except Exception:
        raise ValueError("project_id 非法")

    # 给每个 finding 注入 finding_id / task_id / project_id
    for f in data.get("findings", []):
        if not f.get("finding_id"):
            f["finding_id"] = uuid.uuid4().hex[:12]
        if task_id and not f.get("task_id"):
            f["task_id"] = task_id
        if not f.get("project_id"):
            f["project_id"] = project_id
        if not f.get("url"):
            f["url"] = url

    doc = {
        "project_id": pid,
        "url": url,
        "task_id": task_id,
        "source": source,
        **({"target_id": target_id} if target_id else {}),
        "created_at": _now(),
        "data": data,
    }
    result = await db[WEB_TAGS_COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def list_web_tagging_results(
    db: AsyncIOMotorDatabase,
    project_id: str,
    limit: int = 50,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    列出项目的 web tagging 结果，返回 (items, total)。

    排序规则：
    1. has_findings=true 的在前，false 的在后
    2. 有 findings 的按最高 attention_score 降序
    3. 无 findings 的按 created_at 降序
    """
    try:
        pid = ObjectId(project_id)
    except Exception:
        return [], 0

    total = await db[WEB_TAGS_COLLECTION].count_documents({"project_id": pid})

    pipeline = [
        {"$match": {"project_id": pid}},
        # 计算排序字段：findings 数组是否非空 + 最高 attention_score
        {"$addFields": {
            "_has_findings": {
                "$gt": [{"$size": {"$ifNull": ["$data.findings", []]}}, 0]
            },
            "_max_score": {
                "$cond": {
                    "if": {"$gt": [{"$size": {"$ifNull": ["$data.findings", []]}}, 0]},
                    "then": {"$max": {"$ifNull": ["$data.findings.attention_score", [0]]}},
                    "else": -1,
                }
            },
        }},
        # 有 findings 的在前，然后按最高分降序，最后按时间降序
        {"$sort": {"_has_findings": -1, "_max_score": -1, "created_at": -1}},
        {"$skip": skip},
        {"$limit": limit},
        # 去掉临时字段
        {"$project": {"_has_findings": 0, "_max_score": 0}},
    ]

    items = [doc async for doc in db[WEB_TAGS_COLLECTION].aggregate(pipeline)]
    return items, total


async def delete_web_tagging_results_by_project_id(db: AsyncIOMotorDatabase, project_id: str) -> int:
    try:
        pid = ObjectId(project_id)
    except Exception:
        return 0

    result = await db[WEB_TAGS_COLLECTION].delete_many({"project_id": pid})
    return int(result.deleted_count)
