"""Target 机构深研持久化。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import TARGET_RESEARCH_COLLECTION
from api.dao.project_scope import project_scope_query


def research_id(target_id: str, task_id: str) -> str:
    raw = f"target-research:{target_id}:{task_id}".encode("utf-8")
    return "tri_" + hashlib.sha1(raw).hexdigest()[:20]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[TARGET_RESEARCH_COLLECTION]
    await coll.create_index("research_id", unique=True)
    await coll.create_index([("target_id", 1), ("researched_at", -1)])
    await coll.create_index([("project_id", 1), ("researched_at", -1)])
    await coll.create_index([("project_ids", 1), ("researched_at", -1)])
    await coll.create_index([("project_id", 1), ("target_id", 1), ("is_latest", 1)])
    await coll.create_index("task_id", sparse=True)


async def save_research(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    task_id: str,
    document: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rid = research_id(target_id, task_id)
    existing = await db[TARGET_RESEARCH_COLLECTION].find_one(
        {"research_id": rid}, {"research_version": 1}
    )
    previous = await db[TARGET_RESEARCH_COLLECTION].find_one(
        {"project_id": project_id, "target_id": target_id, "is_latest": True},
        {"research_version": 1},
        sort=[("researched_at", -1)],
    )
    version = int((existing or {}).get("research_version") or 0) or (
        int((previous or {}).get("research_version") or 0) + 1
    )
    await db[TARGET_RESEARCH_COLLECTION].update_many(
        {
            "project_id": project_id,
            "target_id": target_id,
            "is_latest": True,
            "research_id": {"$ne": rid},
        },
        {"$set": {"is_latest": False, "superseded_at": now}},
    )
    payload = {
        **document,
        "research_id": rid,
        "project_id": project_id,
        "target_id": target_id,
        "task_id": task_id,
        "research_version": version,
        "is_latest": True,
        "researched_at": now,
        "updated_at": now,
    }
    payload.pop("project_id", None)
    await db[TARGET_RESEARCH_COLLECTION].update_one(
        {"research_id": rid},
        {
            "$set": payload,
            "$setOnInsert": {"created_at": now, "project_id": project_id},
            "$addToSet": {"project_ids": project_id},
        },
        upsert=True,
    )
    return await db[TARGET_RESEARCH_COLLECTION].find_one(
        {"research_id": rid}, {"_id": 0}
    ) or payload


async def get_latest_research(
    db: AsyncIOMotorDatabase,
    *,
    target_id: str,
    project_id: str = "",
) -> dict[str, Any] | None:
    query: dict[str, Any] = {"target_id": target_id, "is_latest": True}
    if project_id:
        query = project_scope_query(project_id, query)
    return await db[TARGET_RESEARCH_COLLECTION].find_one(
        query, {"_id": 0}, sort=[("researched_at", -1)]
    )


async def list_project_research(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    target_id: str = "",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {}
    if target_id:
        query["target_id"] = target_id
    query = project_scope_query(project_id, query)
    coll = db[TARGET_RESEARCH_COLLECTION]
    total = await coll.count_documents(query)
    bounded = max(1, min(int(limit or 50), 200))
    items = await (
        coll.find(query, {"_id": 0})
        .sort("researched_at", -1)
        .skip(max(0, int(skip or 0)))
        .limit(bounded)
        .to_list(bounded)
    )
    return items, total
