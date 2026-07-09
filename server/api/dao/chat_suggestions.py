"""
系统4 — 聊天建议落库(让前端「随时查看」最新建议,无需重新生成)。

集合 chat_suggestions,按 key(contact_id 或 device:<id>)存最新一份:
{ key, device_id, contact_id, suggestions, screen_analysis, updated_at }
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import CHAT_SUGGESTIONS_COLLECTION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[CHAT_SUGGESTIONS_COLLECTION]
    try:
        await coll.create_index("key", unique=True)
    except Exception:
        pass
    await coll.create_index("contact_id")
    await coll.create_index("device_id")
    await coll.create_index("project_id")
    await coll.create_index("updated_at")


async def save_suggestions(
    db: AsyncIOMotorDatabase, key: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    payload = dict(data)
    payload["key"] = key
    payload["updated_at"] = _now()
    await db[CHAT_SUGGESTIONS_COLLECTION].update_one(
        {"key": key},
        {"$set": payload, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )
    return await get_suggestions(db, key)


async def get_suggestions(
    db: AsyncIOMotorDatabase, key: str
) -> dict[str, Any] | None:
    return await db[CHAT_SUGGESTIONS_COLLECTION].find_one({"key": key}, {"_id": 0})
