"""Persistence for progressively disclosed Skill package resources."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DeleteMany, UpdateOne

from api.db.collections import SKILL_RESOURCES_COLLECTION


_NO_ID = {"_id": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[SKILL_RESOURCES_COLLECTION]
    await collection.create_index(
        [("skill_id", ASCENDING), ("path", ASCENDING)],
        unique=True,
    )
    await collection.create_index(
        [("skill_id", ASCENDING), ("parent_path", ASCENDING), ("kind", ASCENDING)]
    )
    await collection.create_index("content_hash")


async def replace_skill_resources(
    db: AsyncIOMotorDatabase,
    *,
    skill_id: str,
    resources: Iterable[dict[str, Any]],
) -> int:
    """Atomically converge one Skill's resource manifest by stable path."""
    now = _now()
    docs: list[dict[str, Any]] = []
    for resource in resources:
        doc = {
            **resource,
            "skill_id": skill_id,
            "updated_at": now,
        }
        docs.append(doc)

    paths = [str(doc["path"]) for doc in docs]
    operations: list[Any] = [
        UpdateOne(
            {"skill_id": skill_id, "path": doc["path"]},
            {
                "$set": doc,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        for doc in docs
    ]
    stale_filter: dict[str, Any] = {"skill_id": skill_id}
    if paths:
        stale_filter["path"] = {"$nin": paths}
    operations.append(DeleteMany(stale_filter))
    await db[SKILL_RESOURCES_COLLECTION].bulk_write(operations, ordered=False)
    return len(docs)


async def count_skill_resources(db: AsyncIOMotorDatabase, skill_id: str) -> int:
    return await db[SKILL_RESOURCES_COLLECTION].count_documents({"skill_id": skill_id})


async def list_children(
    db: AsyncIOMotorDatabase,
    *,
    skill_id: str,
    parent_path: str = "",
) -> list[dict[str, Any]]:
    projection = {**_NO_ID, "content": 0}
    cursor = db[SKILL_RESOURCES_COLLECTION].find(
        {"skill_id": skill_id, "parent_path": parent_path},
        projection,
    ).sort([("kind", ASCENDING), ("name", ASCENDING)])
    return [doc async for doc in cursor]


async def get_resource(
    db: AsyncIOMotorDatabase,
    *,
    skill_id: str,
    path: str,
    include_content: bool = True,
) -> dict[str, Any] | None:
    projection = _NO_ID if include_content else {**_NO_ID, "content": 0}
    return await db[SKILL_RESOURCES_COLLECTION].find_one(
        {"skill_id": skill_id, "path": path},
        projection,
    )


async def runtime_resources(
    db: AsyncIOMotorDatabase,
    skill_ids: list[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Load resource bodies for the in-memory Layer 3 snapshot."""
    if not skill_ids:
        return {}
    result: dict[str, dict[str, dict[str, Any]]] = {}
    cursor = db[SKILL_RESOURCES_COLLECTION].find(
        {
            "skill_id": {"$in": skill_ids},
            "kind": "file",
            "path": {"$ne": "SKILL.md"},
            "content": {"$exists": True},
        },
        {
            "_id": 0,
            "skill_id": 1,
            "path": 1,
            "role": 1,
            "content": 1,
        },
    )
    async for doc in cursor:
        result.setdefault(str(doc["skill_id"]), {})[str(doc["path"])] = {
            "role": str(doc.get("role") or "resource"),
            "content": str(doc.get("content") or ""),
        }
    return result


async def delete_skill_resources(db: AsyncIOMotorDatabase, skill_id: str) -> int:
    result = await db[SKILL_RESOURCES_COLLECTION].delete_many({"skill_id": skill_id})
    return int(result.deleted_count)
