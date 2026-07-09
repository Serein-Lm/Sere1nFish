"""系统1 — 设备独占预约持久化(重启/查询可恢复)。

集合 device_reservations，**按稳定设备 key（ro.serialno）唯一**——掉线重连
（含 WiFi ip:port 变化）后占用仍能对应回同一台手机。释放即删除。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

DEVICE_RESERVATIONS_COLLECTION = "device_reservations"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[DEVICE_RESERVATIONS_COLLECTION]
    # 稳定 key 唯一（sparse 兼容历史无 device_key 的旧文档）
    try:
        await coll.create_index("device_key", unique=True, sparse=True)
    except Exception:
        pass
    # 历史 device_id 可能是 unique 索引：尽力降级为普通索引，避免重连地址复用冲突
    try:
        await coll.drop_index("device_id_1")
    except Exception:
        pass
    try:
        await coll.create_index("device_id")
    except Exception:
        pass
    try:
        await coll.create_index("owner")
    except Exception:
        pass


async def upsert_reservation(
    db: AsyncIOMotorDatabase,
    device_key: str,
    owner: str,
    *,
    note: str = "",
    since: float | None = None,
    device_id: str = "",
) -> None:
    await db[DEVICE_RESERVATIONS_COLLECTION].update_one(
        {"device_key": device_key},
        {
            "$set": {
                "device_key": device_key,
                "owner": owner,
                "note": note,
                "since": since,
                "device_id": device_id,
                "updated_at": _now(),
            },
            "$setOnInsert": {"created_at": _now()},
        },
        upsert=True,
    )


async def delete_reservation(db: AsyncIOMotorDatabase, device_key: str) -> bool:
    res = await db[DEVICE_RESERVATIONS_COLLECTION].delete_one({"device_key": device_key})
    return res.deleted_count > 0


async def list_reservations(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[DEVICE_RESERVATIONS_COLLECTION].find({}, {"_id": 0})
    return [doc async for doc in cursor]
