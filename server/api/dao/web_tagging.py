from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import WEB_TAGS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _project_id_values(project_id: str) -> list[Any]:
    values: list[Any] = [str(project_id)]
    try:
        object_id = ObjectId(project_id)
    except Exception:
        return values
    if object_id not in values:
        values.append(object_id)
    return values


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[WEB_TAGS_COLLECTION].create_index(
        [("project_id", 1), ("source", 1), ("target_id", 1)]
    )


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
    values = _project_id_values(project_id)
    if len(values) == 1:
        raise ValueError("project_id 非法")
    pid = values[-1]

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
    limit: int | None = 50,
    skip: int = 0,
    source: str = "",
    target_id: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """
    列出项目的 web tagging 结果，返回 (items, total)。

    排序规则：
    1. has_findings=true 的在前，false 的在后
    2. 有 findings 的按最高 attention_score 降序
    3. 无 findings 的按 created_at 降序
    """
    project_values = _project_id_values(project_id)
    query: dict[str, Any] = {"project_id": {"$in": project_values}}
    if source == "web_tagging":
        query["$or"] = [
            {"source": "web_tagging"},
            {"source": {"$exists": False}},
            {"source": None},
        ]
    elif source:
        query["source"] = source
    selected_target_id = str(target_id or "").strip()
    if selected_target_id:
        query["target_id"] = selected_target_id

    total = await db[WEB_TAGS_COLLECTION].count_documents(query)

    pipeline = [
        {"$match": query},
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
    ]
    if skip:
        pipeline.append({"$skip": skip})
    if limit is not None:
        pipeline.append({"$limit": max(1, int(limit))})
    # 去掉临时字段
    pipeline.append({"$project": {"_has_findings": 0, "_max_score": 0}})

    items = [doc async for doc in db[WEB_TAGS_COLLECTION].aggregate(pipeline)]
    return items, total


async def list_web_tagging_identities(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
) -> list[dict[str, Any]]:
    """读取 Target 计数所需的最小网站身份字段。"""
    selected = [str(value or "").strip() for value in target_ids if str(value or "").strip()]
    if not selected:
        return []
    query: dict[str, Any] = {
        "project_id": {"$in": _project_id_values(project_id)},
        "target_id": {"$in": selected},
        "$or": [
            {"source": "web_tagging"},
            {"source": {"$exists": False}},
            {"source": None},
        ],
    }
    projection = {
        "_id": 0,
        "target_id": 1,
        "url": 1,
        "endpoint_key": 1,
        "excluded": 1,
        "intro": 1,
        "data.intro": 1,
    }
    return [
        doc
        async for doc in db[WEB_TAGS_COLLECTION].find(query, projection)
    ]


async def delete_web_tagging_results_by_project_id(db: AsyncIOMotorDatabase, project_id: str) -> int:
    project_values = _project_id_values(project_id)
    result = await db[WEB_TAGS_COLLECTION].delete_many(
        {"project_id": {"$in": project_values}}
    )
    return int(result.deleted_count)
