"""系统1b — 设备分组 + 设备元数据（备注 / 标签 / 显示名），Mongo 持久化。

与 AutoGLM 的文件版 DeviceGroupManager / DeviceMetadataManager **完全解耦**：
- 分组与元数据均存 Mongo（重启自动恢复，可多端共享）。
- 元数据按「稳定设备 key」(`core.mobile.identity.resolve_device_key`，即 ro.serialno)
  存储，掉线重连后仍能对应到同一台设备。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

DEVICE_GROUPS_COLLECTION = "device_groups"
DEVICE_METADATA_COLLECTION = "device_metadata"

_UNSET: Any = object()  # 区分「未提供」与「显式置空」


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    try:
        await db[DEVICE_GROUPS_COLLECTION].create_index("group_id", unique=True)
    except Exception:
        pass
    try:
        await db[DEVICE_METADATA_COLLECTION].create_index("device_key", unique=True)
    except Exception:
        pass
    await db[DEVICE_METADATA_COLLECTION].create_index("group_id")


# ── 分组 CRUD ──

async def create_group(
    db: AsyncIOMotorDatabase, name: str, *, color: str | None = None
) -> dict[str, Any]:
    gid = uuid.uuid4().hex[:12]
    order = await db[DEVICE_GROUPS_COLLECTION].count_documents({})
    doc = {
        "group_id": gid,
        "name": name,
        "color": color,
        "order": order,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db[DEVICE_GROUPS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def list_groups(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[DEVICE_GROUPS_COLLECTION].find({}, {"_id": 0}).sort("order", 1)
    return [d async for d in cursor]


async def get_group(db: AsyncIOMotorDatabase, group_id: str) -> dict[str, Any] | None:
    return await db[DEVICE_GROUPS_COLLECTION].find_one({"group_id": group_id}, {"_id": 0})


async def update_group(
    db: AsyncIOMotorDatabase,
    group_id: str,
    *,
    name: Any = _UNSET,
    color: Any = _UNSET,
    order: Any = _UNSET,
) -> dict[str, Any] | None:
    patch: dict[str, Any] = {}
    if name is not _UNSET:
        patch["name"] = name
    if color is not _UNSET:
        patch["color"] = color
    if order is not _UNSET:
        patch["order"] = order
    if not patch:
        return await get_group(db, group_id)
    patch["updated_at"] = _now()
    return await db[DEVICE_GROUPS_COLLECTION].find_one_and_update(
        {"group_id": group_id},
        {"$set": patch},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )


async def delete_group(db: AsyncIOMotorDatabase, group_id: str) -> bool:
    res = await db[DEVICE_GROUPS_COLLECTION].delete_one({"group_id": group_id})
    if not res.deleted_count:
        return False
    # 解绑该组下所有设备
    await db[DEVICE_METADATA_COLLECTION].update_many(
        {"group_id": group_id},
        {"$set": {"group_id": None, "updated_at": _now()}},
    )
    return True


# ── 设备元数据（备注 / 标签 / 显示名）──

async def get_metadata(
    db: AsyncIOMotorDatabase, device_key: str
) -> dict[str, Any] | None:
    return await db[DEVICE_METADATA_COLLECTION].find_one(
        {"device_key": device_key}, {"_id": 0}
    )


async def get_metadata_map(
    db: AsyncIOMotorDatabase, device_keys: Any
) -> dict[str, dict[str, Any]]:
    """批量取元数据，返回 {device_key: doc}（单次 $in，避免 N+1）。"""
    keys = list(device_keys)
    if not keys:
        return {}
    cursor = db[DEVICE_METADATA_COLLECTION].find(
        {"device_key": {"$in": keys}}, {"_id": 0}
    )
    return {d["device_key"]: d async for d in cursor}


async def list_metadata(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[DEVICE_METADATA_COLLECTION].find({}, {"_id": 0})
    return [d async for d in cursor]


async def upsert_metadata(
    db: AsyncIOMotorDatabase,
    device_key: str,
    *,
    display_name: Any = _UNSET,
    note: Any = _UNSET,
    tags: Any = _UNSET,
    group_id: Any = _UNSET,
    last_device_id: str | None = None,
) -> dict[str, Any] | None:
    """部分更新设备元数据（仅写入显式提供的字段）。不存在则创建。"""
    patch: dict[str, Any] = {"updated_at": _now()}
    if display_name is not _UNSET:
        patch["display_name"] = display_name
    if note is not _UNSET:
        patch["note"] = note
    if tags is not _UNSET:
        patch["tags"] = list(tags or [])
    if group_id is not _UNSET:
        patch["group_id"] = group_id
    if last_device_id is not None:
        patch["last_device_id"] = last_device_id

    await db[DEVICE_METADATA_COLLECTION].update_one(
        {"device_key": device_key},
        {"$set": patch, "$setOnInsert": {"device_key": device_key, "created_at": _now()}},
        upsert=True,
    )
    return await get_metadata(db, device_key)
