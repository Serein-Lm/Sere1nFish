"""
AI 中枢（钓鱼中台）对话留存 DAO。

- conversations：会话元信息（标题、时间、消息计数）。
- messages：会话内消息明细（role/content/workflow）。

会话与消息分表存储，按 conversation_id 关联；删除会话时级联删除其消息。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import (
    AI_HUB_CONVERSATIONS_COLLECTION,
    AI_HUB_MESSAGES_COLLECTION,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """幂等建立索引。"""
    conv = db[AI_HUB_CONVERSATIONS_COLLECTION]
    await conv.create_index("conversation_id", unique=True)
    await conv.create_index("owner")
    await conv.create_index("updated_at")

    msg = db[AI_HUB_MESSAGES_COLLECTION]
    await msg.create_index("message_id", unique=True)
    await msg.create_index([("conversation_id", 1), ("created_at", 1)])


# ── 会话 ─────────────────────────────────────────────

async def create_conversation(
    db: AsyncIOMotorDatabase,
    *,
    title: str = "",
    owner: str = "",
) -> dict[str, Any]:
    now = _now()
    cid = "conv_" + uuid.uuid4().hex[:20]
    doc = {
        "conversation_id": cid,
        "title": (title or "新会话").strip(),
        "owner": owner,
        "message_count": 0,
        "last_message_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db[AI_HUB_CONVERSATIONS_COLLECTION].insert_one(dict(doc))
    return doc


async def list_conversations(
    db: AsyncIOMotorDatabase,
    *,
    owner: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if owner:
        query["owner"] = owner
    cursor = (
        db[AI_HUB_CONVERSATIONS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("updated_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def get_conversation(
    db: AsyncIOMotorDatabase, conversation_id: str
) -> dict[str, Any] | None:
    return await db[AI_HUB_CONVERSATIONS_COLLECTION].find_one(
        {"conversation_id": conversation_id}, {"_id": 0}
    )


async def rename_conversation(
    db: AsyncIOMotorDatabase, conversation_id: str, title: str
) -> dict[str, Any] | None:
    await db[AI_HUB_CONVERSATIONS_COLLECTION].update_one(
        {"conversation_id": conversation_id},
        {"$set": {"title": title.strip(), "updated_at": _now()}},
    )
    return await get_conversation(db, conversation_id)


async def delete_conversation(
    db: AsyncIOMotorDatabase, conversation_id: str
) -> dict[str, int]:
    msg_result = await db[AI_HUB_MESSAGES_COLLECTION].delete_many(
        {"conversation_id": conversation_id}
    )
    conv_result = await db[AI_HUB_CONVERSATIONS_COLLECTION].delete_one(
        {"conversation_id": conversation_id}
    )
    return {
        "conversations_deleted": conv_result.deleted_count,
        "messages_deleted": msg_result.deleted_count,
    }


# ── 消息 ─────────────────────────────────────────────

async def append_message(
    db: AsyncIOMotorDatabase,
    *,
    conversation_id: str,
    role: str,
    content: str,
    workflow: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """追加一条消息，并同步更新会话计数与时间。"""
    now = _now()
    mid = "msg_" + uuid.uuid4().hex[:20]
    doc = {
        "message_id": mid,
        "conversation_id": conversation_id,
        "role": role,
        "content": content or "",
        "workflow": workflow,
        "meta": meta or {},
        "created_at": now,
    }
    await db[AI_HUB_MESSAGES_COLLECTION].insert_one(dict(doc))
    await db[AI_HUB_CONVERSATIONS_COLLECTION].update_one(
        {"conversation_id": conversation_id},
        {
            "$inc": {"message_count": 1},
            "$set": {"last_message_at": now, "updated_at": now},
        },
    )
    return doc


async def list_messages(
    db: AsyncIOMotorDatabase,
    conversation_id: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    cursor = (
        db[AI_HUB_MESSAGES_COLLECTION]
        .find({"conversation_id": conversation_id}, {"_id": 0})
        .sort("created_at", 1)
        .limit(limit)
    )
    return [doc async for doc in cursor]
