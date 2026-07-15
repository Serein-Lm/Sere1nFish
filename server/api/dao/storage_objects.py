"""统一对象存储元数据 DAO。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import STORAGE_OBJECTS_COLLECTION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[STORAGE_OBJECTS_COLLECTION]
    await coll.create_index("object_id", unique=True)
    await coll.create_index([("provider", 1), ("bucket", 1), ("object_key", 1)], unique=True)
    await coll.create_index([("project_id", 1), ("kind", 1), ("created_at", -1)])
    await coll.create_index([("owner", 1), ("created_at", -1)])
    await coll.create_index([("conversation_id", 1), ("created_at", -1)])
    await coll.create_index([("subject_id", 1), ("created_at", -1)])
    await coll.create_index([("source", 1), ("source_id", 1)])
    await coll.create_index("legacy_path", sparse=True)
    await coll.create_index("meta.relative_path", sparse=True)
    await coll.create_index("status")


async def create_pending(
    db: AsyncIOMotorDatabase,
    *,
    object_id: str,
    provider: str,
    bucket: str,
    object_key: str,
    kind: str,
    filename: str,
    content_type: str,
    size: int,
    sha256: str,
    owner: str = "",
    project_id: str = "",
    conversation_id: str = "",
    subject_id: str = "",
    source: str = "",
    source_id: str = "",
    legacy_path: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()
    doc = {
        "object_id": object_id,
        "provider": provider,
        "bucket": bucket,
        "object_key": object_key,
        "kind": kind,
        "filename": filename,
        "content_type": content_type,
        "size": int(size),
        "sha256": sha256,
        "owner": owner,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "subject_id": subject_id,
        "source": source,
        "source_id": source_id,
        "legacy_path": legacy_path,
        "meta": meta or {},
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    await db[STORAGE_OBJECTS_COLLECTION].update_one(
        {"object_id": object_id},
        {"$setOnInsert": doc},
        upsert=True,
    )
    return await get_object(db, object_id) or doc


async def mark_ready(
    db: AsyncIOMotorDatabase,
    object_id: str,
    *,
    etag: str = "",
    version_id: str = "",
    crc64: str = "",
) -> dict[str, Any] | None:
    now = _now()
    await db[STORAGE_OBJECTS_COLLECTION].update_one(
        {"object_id": object_id},
        {
            "$set": {
                "status": "ready",
                "etag": etag,
                "version_id": version_id,
                "crc64": crc64,
                "uploaded_at": now,
                "updated_at": now,
            },
            "$unset": {"error": ""},
        },
    )
    return await get_object(db, object_id)


async def prepare_relocation(
    db: AsyncIOMotorDatabase,
    object_id: str,
    *,
    provider: str,
    bucket: str,
    object_key: str,
) -> None:
    await db[STORAGE_OBJECTS_COLLECTION].update_one(
        {"object_id": object_id},
        {
            "$set": {
                "provider": provider,
                "bucket": bucket,
                "object_key": object_key,
                "status": "pending",
                "updated_at": _now(),
            },
            "$unset": {"error": "", "deleted_at": ""},
        },
    )


async def restore_relocation(
    db: AsyncIOMotorDatabase,
    object_id: str,
    previous: dict[str, Any],
    *,
    error: str,
) -> None:
    """上传目标 Provider 失败时恢复原对象位置，并保留失败诊断。"""
    fields = {
        key: previous.get(key)
        for key in (
            "provider",
            "bucket",
            "object_key",
            "status",
            "etag",
            "version_id",
            "crc64",
            "uploaded_at",
            "deleted_at",
        )
        if key in previous
    }
    fields.update({"last_relocation_error": str(error)[:1000], "updated_at": _now()})
    await db[STORAGE_OBJECTS_COLLECTION].update_one(
        {"object_id": object_id},
        {"$set": fields, "$unset": {"error": ""}},
    )


async def mark_error(db: AsyncIOMotorDatabase, object_id: str, error: str) -> None:
    await db[STORAGE_OBJECTS_COLLECTION].update_one(
        {"object_id": object_id},
        {"$set": {"status": "error", "error": str(error)[:1000], "updated_at": _now()}},
    )


async def mark_deleted(db: AsyncIOMotorDatabase, object_id: str) -> None:
    await db[STORAGE_OBJECTS_COLLECTION].update_one(
        {"object_id": object_id},
        {"$set": {"status": "deleted", "deleted_at": _now(), "updated_at": _now()}},
    )


async def get_object(db: AsyncIOMotorDatabase, object_id: str) -> dict[str, Any] | None:
    return await db[STORAGE_OBJECTS_COLLECTION].find_one({"object_id": object_id}, {"_id": 0})


async def get_by_source(
    db: AsyncIOMotorDatabase,
    *,
    source: str,
    source_id: str,
) -> dict[str, Any] | None:
    return await db[STORAGE_OBJECTS_COLLECTION].find_one(
        {"source": source, "source_id": source_id, "status": "ready"},
        {"_id": 0},
    )


async def get_by_relative_path(
    db: AsyncIOMotorDatabase,
    relative_path: str,
) -> dict[str, Any] | None:
    return await db[STORAGE_OBJECTS_COLLECTION].find_one(
        {"meta.relative_path": relative_path, "status": "ready"},
        {"_id": 0},
    )


async def list_objects(
    db: AsyncIOMotorDatabase,
    *,
    kind: str = "",
    project_id: str = "",
    owner: str = "",
    status: str = "ready",
    limit: int = 100,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if kind:
        query["kind"] = kind
    if project_id:
        query["project_id"] = project_id
    if owner:
        query["owner"] = owner
    if status:
        query["status"] = status
    cursor = (
        db[STORAGE_OBJECTS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(max(1, min(limit, 1000)))
    )
    return [doc async for doc in cursor]


async def get_stats(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    rows = await db[STORAGE_OBJECTS_COLLECTION].aggregate(
        [
            {
                "$group": {
                    "_id": {"provider": "$provider", "status": "$status"},
                    "count": {"$sum": 1},
                    "bytes": {"$sum": {"$ifNull": ["$size", 0]}},
                }
            }
        ]
    ).to_list(length=None)
    providers: dict[str, dict[str, dict[str, int]]] = {}
    total_count = 0
    total_bytes = 0
    ready_count = 0
    ready_bytes = 0
    for row in rows:
        group = row.get("_id") or {}
        provider = str(group.get("provider") or "unknown")
        status = str(group.get("status") or "unknown")
        count = int(row.get("count") or 0)
        size = int(row.get("bytes") or 0)
        providers.setdefault(provider, {})[status] = {"count": count, "bytes": size}
        total_count += count
        total_bytes += size
        if status == "ready":
            ready_count += count
            ready_bytes += size
    return {
        "count": total_count,
        "bytes": total_bytes,
        "ready_count": ready_count,
        "ready_bytes": ready_bytes,
        "providers": providers,
    }
