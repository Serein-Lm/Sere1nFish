from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import PROJECT_GROUPS_COLLECTION, PROJECTS_COLLECTION


_NO_ID = {"_id": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_group_id() -> str:
    return f"pg_{uuid4().hex[:20]}"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[PROJECT_GROUPS_COLLECTION]
    await collection.create_index("group_id", unique=True)
    await collection.create_index("name", unique=True)
    await collection.create_index([("sort_order", 1), ("created_at", 1)])


async def create_group(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    description: str = "",
    sort_order: int = 0,
) -> dict[str, Any]:
    now = _now()
    doc = {
        "group_id": _new_group_id(),
        "name": name,
        "description": description,
        "sort_order": sort_order,
        "created_at": now,
        "updated_at": now,
    }
    await db[PROJECT_GROUPS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return {**doc, "project_count": 0}


async def get_group(
    db: AsyncIOMotorDatabase,
    group_id: str,
) -> dict[str, Any] | None:
    return await db[PROJECT_GROUPS_COLLECTION].find_one(
        {"group_id": group_id},
        _NO_ID,
    )


async def get_group_by_name(
    db: AsyncIOMotorDatabase,
    name: str,
) -> dict[str, Any] | None:
    return await db[PROJECT_GROUPS_COLLECTION].find_one({"name": name}, _NO_ID)


async def get_group_names(
    db: AsyncIOMotorDatabase,
    group_ids: list[str],
) -> dict[str, str]:
    normalized_ids = list(dict.fromkeys(value for value in group_ids if value))
    if not normalized_ids:
        return {}
    cursor = db[PROJECT_GROUPS_COLLECTION].find(
        {"group_id": {"$in": normalized_ids}},
        {"_id": 0, "group_id": 1, "name": 1},
    )
    return {
        str(doc["group_id"]): str(doc.get("name") or "")
        async for doc in cursor
    }


async def list_groups(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    count_cursor = db[PROJECTS_COLLECTION].aggregate(
        [
            {
                "$match": {
                    "group_id": {"$exists": True, "$nin": [None, ""]},
                    "archived_at": {"$exists": False},
                }
            },
            {"$group": {"_id": "$group_id", "count": {"$sum": 1}}},
        ]
    )
    counts = {
        str(doc["_id"]): int(doc.get("count") or 0)
        async for doc in count_cursor
    }
    cursor = db[PROJECT_GROUPS_COLLECTION].find({}, _NO_ID).sort(
        [("sort_order", 1), ("created_at", 1)]
    )
    return [
        {**doc, "project_count": counts.get(str(doc.get("group_id") or ""), 0)}
        async for doc in cursor
    ]


async def update_group(
    db: AsyncIOMotorDatabase,
    group_id: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    fields = dict(patch or {})
    fields["updated_at"] = _now()
    doc = await db[PROJECT_GROUPS_COLLECTION].find_one_and_update(
        {"group_id": group_id},
        {"$set": fields},
        projection=_NO_ID,
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        return None
    project_count = await db[PROJECTS_COLLECTION].count_documents(
        {"group_id": group_id, "archived_at": {"$exists": False}}
    )
    return {**doc, "project_count": project_count}


async def delete_group(
    db: AsyncIOMotorDatabase,
    group_id: str,
) -> tuple[bool, int]:
    if not await get_group(db, group_id):
        return False, 0
    ungrouped = await db[PROJECTS_COLLECTION].update_many(
        {"group_id": group_id},
        {"$unset": {"group_id": ""}, "$set": {"updated_at": _now()}},
    )
    deleted = await db[PROJECT_GROUPS_COLLECTION].delete_one(
        {"group_id": group_id}
    )
    return bool(deleted.deleted_count), int(ungrouped.modified_count or 0)
