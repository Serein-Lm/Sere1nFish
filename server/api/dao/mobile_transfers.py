"""手机文件传输记录 DAO。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import MOBILE_TRANSFERS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[MOBILE_TRANSFERS_COLLECTION]
    await collection.create_index("transfer_id", unique=True)
    await collection.create_index([("device_id", 1), ("created_at", -1)])
    await collection.create_index([("owner", 1), ("created_at", -1)])
    await collection.create_index("storage_object_id", sparse=True)


async def create_transfer(
    db: AsyncIOMotorDatabase,
    *,
    transfer_id: str,
    device_id: str,
    owner: str,
    filename: str,
    content_type: str,
    category: str,
    size: int,
) -> dict[str, Any]:
    now = _now()
    doc = {
        "transfer_id": transfer_id,
        "device_id": device_id,
        "owner": owner,
        "filename": filename,
        "content_type": content_type,
        "category": category,
        "size": size,
        "status": "archiving",
        "storage_object_id": "",
        "remote_path": "",
        "attempts": 0,
        "last_error": "",
        "created_at": now,
        "updated_at": now,
    }
    await db[MOBILE_TRANSFERS_COLLECTION].insert_one(doc)
    return {key: value for key, value in doc.items() if key != "_id"}


async def mark_archived(
    db: AsyncIOMotorDatabase,
    transfer_id: str,
    storage_object_id: str,
) -> None:
    await db[MOBILE_TRANSFERS_COLLECTION].update_one(
        {"transfer_id": transfer_id},
        {
            "$set": {
                "storage_object_id": storage_object_id,
                "status": "pushing",
                "updated_at": _now(),
            }
        },
    )


async def mark_push_started(db: AsyncIOMotorDatabase, transfer_id: str) -> None:
    await db[MOBILE_TRANSFERS_COLLECTION].update_one(
        {"transfer_id": transfer_id},
        {
            "$set": {"status": "pushing", "last_error": "", "updated_at": _now()},
            "$inc": {"attempts": 1},
        },
    )


async def mark_completed(
    db: AsyncIOMotorDatabase,
    transfer_id: str,
    *,
    remote_path: str,
    adb_endpoint: str,
) -> dict[str, Any] | None:
    now = _now()
    await db[MOBILE_TRANSFERS_COLLECTION].update_one(
        {"transfer_id": transfer_id},
        {
            "$set": {
                "status": "completed",
                "remote_path": remote_path,
                "adb_endpoint": adb_endpoint,
                "last_error": "",
                "completed_at": now,
                "updated_at": now,
            }
        },
    )
    return await get_transfer(db, transfer_id)


async def mark_failed(
    db: AsyncIOMotorDatabase,
    transfer_id: str,
    error: str,
) -> dict[str, Any] | None:
    await db[MOBILE_TRANSFERS_COLLECTION].update_one(
        {"transfer_id": transfer_id},
        {
            "$set": {
                "status": "failed",
                "last_error": str(error)[:1000],
                "updated_at": _now(),
            }
        },
    )
    return await get_transfer(db, transfer_id)


async def get_transfer(
    db: AsyncIOMotorDatabase, transfer_id: str
) -> dict[str, Any] | None:
    return await db[MOBILE_TRANSFERS_COLLECTION].find_one(
        {"transfer_id": transfer_id}, {"_id": 0}
    )


async def list_transfers(
    db: AsyncIOMotorDatabase,
    *,
    device_id: str,
    owner: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"device_id": device_id}
    if owner:
        query["owner"] = owner
    cursor = (
        db[MOBILE_TRANSFERS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(max(1, min(limit, 200)))
    )
    return [doc async for doc in cursor]
