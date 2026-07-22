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
from pymongo import ReturnDocument

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
    await msg.create_index(
        [("conversation_id", 1), ("context_version", 1), ("created_at", 1)]
    )


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
        "context_version": 0,
        "last_message_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db[AI_HUB_CONVERSATIONS_COLLECTION].insert_one(dict(doc))
    return doc


async def ensure_conversation(
    db: AsyncIOMotorDatabase,
    *,
    conversation_id: str,
    title: str = "",
    owner: str = "",
) -> dict[str, Any]:
    """Idempotently create a stable external-channel conversation."""
    now = _now()
    await db[AI_HUB_CONVERSATIONS_COLLECTION].update_one(
        {"conversation_id": conversation_id},
        {
            "$setOnInsert": {
                "conversation_id": conversation_id,
                "title": (title or "外部会话").strip(),
                "owner": owner,
                "message_count": 0,
                "context_version": 0,
                "last_message_at": None,
                "created_at": now,
                "updated_at": now,
            }
        },
        upsert=True,
    )
    return await get_conversation(db, conversation_id) or {}


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


async def clear_conversation_messages(
    db: AsyncIOMotorDatabase, conversation_id: str
) -> dict[str, int]:
    """Clear one conversation without deleting its metadata or artifacts.

    Advancing ``context_version`` invalidates completions that started before
    the clear operation, so an in-flight AI request cannot restore stale
    context after it finishes.
    """
    conversation = await db[AI_HUB_CONVERSATIONS_COLLECTION].find_one_and_update(
        {"conversation_id": conversation_id},
        {
            "$inc": {"context_version": 1},
            "$set": {
                "message_count": 0,
                "last_message_at": None,
                "updated_at": _now(),
            },
        },
        projection={"_id": 0, "context_version": 1},
        return_document=ReturnDocument.AFTER,
    )
    msg_result = await db[AI_HUB_MESSAGES_COLLECTION].delete_many(
        {"conversation_id": conversation_id}
    )
    return {
        "messages_deleted": msg_result.deleted_count,
        "context_version": int((conversation or {}).get("context_version") or 0),
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
    context_version: int | None = None,
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
    if context_version is not None:
        doc["context_version"] = context_version
    await db[AI_HUB_MESSAGES_COLLECTION].insert_one(dict(doc))
    conversation_filter = _context_version_filter(conversation_id, context_version)
    update_result = await db[AI_HUB_CONVERSATIONS_COLLECTION].update_one(
        conversation_filter,
        {
            "$inc": {"message_count": 1},
            "$set": {"last_message_at": now, "updated_at": now},
        },
    )
    if context_version is not None and not update_result.matched_count:
        await db[AI_HUB_MESSAGES_COLLECTION].delete_one({"message_id": mid})
        return {}
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


async def list_recent_messages(
    db: AsyncIOMotorDatabase,
    conversation_id: str,
    *,
    limit: int = 12,
    context_version: int | None = None,
) -> list[dict[str, Any]]:
    """Return the newest bounded message window in chronological order."""
    bounded_limit = max(1, min(int(limit or 12), 50))
    cursor = (
        db[AI_HUB_MESSAGES_COLLECTION]
        .find(_context_version_filter(conversation_id, context_version), {"_id": 0})
        .sort("created_at", -1)
        .limit(bounded_limit)
    )
    messages = [doc async for doc in cursor]
    messages.reverse()
    return messages


def _context_version_filter(
    conversation_id: str, context_version: int | None
) -> dict[str, Any]:
    query: dict[str, Any] = {"conversation_id": conversation_id}
    if context_version is None:
        return query
    if context_version == 0:
        query["$or"] = [
            {"context_version": 0},
            {"context_version": {"$exists": False}},
        ]
    else:
        query["context_version"] = context_version
    return query
