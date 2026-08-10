from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import PROJECTS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _oid_str(oid: ObjectId) -> str:
    return str(oid)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[PROJECTS_COLLECTION]
    await collection.create_index([("group_id", 1), ("updated_at", -1)])
    await collection.create_index([("name", 1), ("updated_at", -1)])
    await collection.create_index([("archived_at", 1), ("updated_at", -1)])


async def create_project(
    db: AsyncIOMotorDatabase,
    name: str,
    description: str | None = None,
    group_id: str | None = None,
) -> dict[str, Any]:
    now = _now()
    doc = {
        "name": name,
        "description": description,
        "target": None,
        "contents": [],
        "created_at": now,
        "updated_at": now,
    }
    if group_id:
        doc["group_id"] = group_id
    result = await db[PROJECTS_COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def get_project_by_name(db: AsyncIOMotorDatabase, name: str) -> dict[str, Any] | None:
    return await db[PROJECTS_COLLECTION].find_one({"name": name})


async def upsert_get_project_by_name(
    db: AsyncIOMotorDatabase,
    name: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Upsert 获取项目，已存在则返回，不存在则创建"""
    now = _now()
    # 先查询是否存在
    existing = await db[PROJECTS_COLLECTION].find_one({"name": name})
    if existing:
        return existing
    
    # 不存在则创建
    doc = await db[PROJECTS_COLLECTION].find_one_and_update(
        {"name": name},
        {
            "$setOnInsert": {
                "name": name,
                "description": description,
                "created_at": now,
                "updated_at": now,
                "target": None,
                "contents": [],
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def list_projects(
    db: AsyncIOMotorDatabase,
    limit: int = 50,
    skip: int = 0,
    *,
    group_id: str | None = None,
    search: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """列出项目，返回 (items, total)"""
    query: dict[str, Any] = {"archived_at": {"$exists": False}}
    if group_id:
        query["group_id"] = group_id
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = re.escape(normalized_search)
        query["$or"] = [
            {"name": {"$regex": pattern, "$options": "i"}},
            {"description": {"$regex": pattern, "$options": "i"}},
        ]
    total = await db[PROJECTS_COLLECTION].count_documents(query)
    cursor = db[PROJECTS_COLLECTION].find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = [doc async for doc in cursor]
    return items, total


async def get_project(db: AsyncIOMotorDatabase, project_id: str) -> dict[str, Any] | None:
    try:
        oid = ObjectId(project_id)
    except Exception:
        return None
    return await db[PROJECTS_COLLECTION].find_one({"_id": oid})


async def touch_project(db: AsyncIOMotorDatabase, project_id: str) -> None:
    try:
        oid = ObjectId(project_id)
    except Exception:
        return
    await db[PROJECTS_COLLECTION].update_one({"_id": oid}, {"$set": {"updated_at": _now()}})


async def update_project(
    db: AsyncIOMotorDatabase,
    project_id: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        oid = ObjectId(project_id)
    except Exception:
        return None

    update_set = dict(patch or {})
    unset: dict[str, str] = {}
    if "group_id" in update_set and not update_set["group_id"]:
        update_set.pop("group_id")
        unset["group_id"] = ""
    update_set["updated_at"] = _now()

    update: dict[str, Any] = {"$set": update_set}
    if unset:
        update["$unset"] = unset

    result = await db[PROJECTS_COLLECTION].find_one_and_update(
        {"_id": oid},
        update,
        return_document=ReturnDocument.AFTER,
    )
    return result


async def delete_project(db: AsyncIOMotorDatabase, project_id: str) -> bool:
    try:
        oid = ObjectId(project_id)
    except Exception:
        return False

    result = await db[PROJECTS_COLLECTION].delete_one({"_id": oid})
    return bool(result.deleted_count)


async def archive_project(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    reason: str,
    merged_into_project_ids: list[str],
    merge_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Hide a merged project while retaining its audit and evidence history."""
    now = _now()
    return await update_project(
        db,
        project_id,
        {
            "archived_at": now,
            "archive_reason": str(reason or "project_data_merged"),
            "merged_into_project_ids": list(
                dict.fromkeys(
                    str(value or "").strip()
                    for value in merged_into_project_ids
                    if str(value or "").strip()
                )
            ),
            "merge_summary": dict(merge_summary or {}),
        },
    )


async def append_project_content(
    db: AsyncIOMotorDatabase,
    project_id: str,
    content: str,
    target: str | None = None,
) -> dict[str, Any] | None:
    try:
        oid = ObjectId(project_id)
    except Exception:
        return None

    update: dict[str, Any] = {
        "$push": {"contents": content},
        "$set": {"updated_at": _now()},
    }
    if target is not None:
        update["$set"]["target"] = target

    return await db[PROJECTS_COLLECTION].find_one_and_update(
        {"_id": oid},
        update,
        return_document=ReturnDocument.AFTER,
    )
