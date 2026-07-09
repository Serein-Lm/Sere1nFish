from __future__ import annotations

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


async def create_project(db: AsyncIOMotorDatabase, name: str, description: str | None = None) -> dict[str, Any]:
    now = _now()
    doc = {
        "name": name,
        "description": description,
        "target": None,
        "contents": [],
        "created_at": now,
        "updated_at": now,
    }
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


async def list_projects(db: AsyncIOMotorDatabase, limit: int = 50, skip: int = 0) -> tuple[list[dict[str, Any]], int]:
    """列出项目，返回 (items, total)"""
    query: dict[str, Any] = {}
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
    update_set["updated_at"] = _now()

    result = await db[PROJECTS_COLLECTION].find_one_and_update(
        {"_id": oid},
        {"$set": update_set},
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
