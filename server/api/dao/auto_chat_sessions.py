"""
系统5 — 自动聊天会话快照落库(可查询/审计;重启后会话需重新启动)。

集合 auto_chat_sessions,按 task_id 存最新快照。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import AUTO_CHAT_SESSIONS_COLLECTION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[AUTO_CHAT_SESSIONS_COLLECTION]
    try:
        await coll.create_index("task_id", unique=True)
    except Exception:
        pass
    await coll.create_index("device_id")
    await coll.create_index("contact_id")
    await coll.create_index("project_id")
    await coll.create_index("updated_at")


async def upsert_session(
    db: AsyncIOMotorDatabase, snapshot: dict[str, Any]
) -> None:
    task_id = snapshot.get("task_id")
    if not task_id:
        return
    payload = dict(snapshot)
    payload["updated_at"] = _now()
    await db[AUTO_CHAT_SESSIONS_COLLECTION].update_one(
        {"task_id": task_id},
        {"$set": payload, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )


async def list_sessions(
    db: AsyncIOMotorDatabase,
    *,
    device_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if device_id:
        query["device_id"] = device_id
    if project_id:
        query["project_id"] = project_id
    cursor = (
        db[AUTO_CHAT_SESSIONS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]
