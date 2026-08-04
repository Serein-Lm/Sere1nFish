"""
AI 中枢（钓鱼中台）对话留存 DAO。

- conversations：会话元信息（标题、时间、消息计数）。
- messages：会话内消息明细（role/content/workflow）。

会话与消息分表存储，按 conversation_id 关联；删除会话时级联删除其消息。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db.collections import (
    AI_HUB_CONVERSATIONS_COLLECTION,
    AI_HUB_MESSAGES_COLLECTION,
    AI_HUB_TURNS_COLLECTION,
)
from api.utils.config_crypto import decrypt_config, encrypt_value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_datetime() -> datetime:
    return datetime.now(timezone.utc)


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

    turns = db[AI_HUB_TURNS_COLLECTION]
    await turns.create_index("turn_id", unique=True)
    await turns.create_index(
        [("channel", 1), ("bot_name", 1), ("external_message_id", 1)],
        unique=True,
        sparse=True,
    )
    await turns.create_index([("bot_name", 1), ("status", 1), ("updated_at", 1)])
    await turns.create_index([("conversation_id", 1), ("created_at", -1)])
    await turns.create_index(
        "expires_at",
        expireAfterSeconds=0,
        partialFilterExpression={"status": {"$in": ["completed", "failed", "cancelled"]}},
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


async def cancel_conversation_turns(
    db: AsyncIOMotorDatabase,
    conversation_id: str,
) -> int:
    """Cancel unfinished external turns when a user explicitly clears context."""
    now = _now_datetime()
    result = await db[AI_HUB_TURNS_COLLECTION].update_many(
        {
            "conversation_id": conversation_id,
            "status": {
                "$in": ["queued", "running", "interrupted", "response_ready"]
            },
        },
        {
            "$set": {
                "status": "cancelled",
                "last_error": "用户已清空会话上下文",
                "updated_at": now,
                "completed_at": now,
                "expires_at": datetime.fromtimestamp(
                    now.timestamp() + 7 * 24 * 60 * 60,
                    tz=timezone.utc,
                ),
            }
        },
    )
    return int(result.modified_count)


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
    message_id: str = "",
) -> dict[str, Any]:
    """Append a message and update conversation counters idempotently."""
    now = _now()
    mid = str(message_id or "").strip() or "msg_" + uuid.uuid4().hex[:20]
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
    try:
        await db[AI_HUB_MESSAGES_COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError:
        return await db[AI_HUB_MESSAGES_COLLECTION].find_one(
            {"message_id": mid}, {"_id": 0}
        ) or {}
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


async def get_message_by_id(
    db: AsyncIOMotorDatabase,
    message_id: str,
) -> dict[str, Any] | None:
    """Read one stable message identity for idempotent external-turn recovery."""
    normalized = str(message_id or "").strip()
    if not normalized:
        return None
    return await db[AI_HUB_MESSAGES_COLLECTION].find_one(
        {"message_id": normalized},
        {"_id": 0},
    )


# ── 外部渠道可恢复轮次 ──────────────────────────────────

_TURN_RETAIN_SECONDS = 7 * 24 * 60 * 60
_MAX_TURN_ATTEMPTS = 3


def external_turn_id(
    *,
    channel: str,
    bot_name: str,
    external_message_id: str,
) -> str:
    """Build a stable turn identity for one external delivery."""
    raw = "\x1f".join(
        [
            str(channel or "").strip(),
            str(bot_name or "").strip(),
            str(external_message_id or "").strip(),
        ]
    ).encode("utf-8")
    return "turn_" + hashlib.sha256(raw).hexdigest()[:24]


async def ensure_external_turn(
    db: AsyncIOMotorDatabase,
    *,
    turn_id: str,
    external_message_id: str,
    conversation_id: str,
    owner: str,
    channel: str,
    bot_name: str,
    sender_id: str,
    query: str,
    session_webhook: str,
) -> dict[str, Any]:
    """Persist a recoverable external turn without storing callback secrets in plaintext."""
    now = _now_datetime()
    callback = {
        "session_webhook": encrypt_value(str(session_webhook or "").strip())
    }
    await db[AI_HUB_TURNS_COLLECTION].update_one(
        {"turn_id": turn_id},
        {
            "$set": {
                "callback": callback,
                "updated_at": now,
            },
            "$setOnInsert": {
                "turn_id": turn_id,
                "external_message_id": str(external_message_id or "").strip(),
                "conversation_id": conversation_id,
                "owner": owner,
                "channel": channel,
                "bot_name": bot_name,
                "sender_id": sender_id,
                "query": query,
                "status": "queued",
                "attempts": 0,
                "last_error": "",
                "created_at": now,
            },
        },
        upsert=True,
    )
    return await get_external_turn(db, turn_id, decrypt_callback=False) or {}


async def get_external_turn(
    db: AsyncIOMotorDatabase,
    turn_id: str,
    *,
    decrypt_callback: bool = True,
) -> dict[str, Any] | None:
    item = await db[AI_HUB_TURNS_COLLECTION].find_one(
        {"turn_id": turn_id}, {"_id": 0}
    )
    if item and decrypt_callback:
        item = dict(item)
        item["callback"] = decrypt_config(item.get("callback") or {})
    return item


def _claim_filter(
    turn_id: str,
    worker_id: str,
    *,
    recovery: bool,
) -> dict[str, Any]:
    states: list[dict[str, Any]] = [
        {
            "status": {"$in": ["queued", "interrupted"]},
            "attempts": {"$lt": _MAX_TURN_ATTEMPTS},
        }
    ]
    if recovery:
        states.extend(
            [
                {"status": "response_ready"},
                {
                    "status": "running",
                    "lease_owner": {"$ne": worker_id},
                    "attempts": {"$lt": _MAX_TURN_ATTEMPTS},
                },
            ]
        )
    return {"turn_id": turn_id, "$or": states}


async def claim_external_turn(
    db: AsyncIOMotorDatabase,
    *,
    turn_id: str,
    worker_id: str,
    recovery: bool = False,
) -> dict[str, Any] | None:
    """Atomically claim a queued turn or take over a turn from an old process."""
    now = _now_datetime()
    previous = await db[AI_HUB_TURNS_COLLECTION].find_one_and_update(
        _claim_filter(turn_id, worker_id, recovery=recovery),
        {
            "$set": {
                "status": "running",
                "lease_owner": worker_id,
                "started_at": now,
                "updated_at": now,
                "last_error": "",
            },
            "$inc": {"attempts": 1},
        },
        projection={"_id": 0},
        return_document=ReturnDocument.BEFORE,
    )
    if not previous:
        return None
    result = dict(previous)
    result["_claimed_from_status"] = str(previous.get("status") or "")
    result["status"] = "running"
    result["lease_owner"] = worker_id
    result["attempts"] = int(previous.get("attempts") or 0) + 1
    result["callback"] = decrypt_config(previous.get("callback") or {})
    return result


async def claim_recoverable_turns(
    db: AsyncIOMotorDatabase,
    *,
    bot_name: str,
    worker_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Claim unfinished turns belonging to a bot after process startup."""
    bounded = max(1, min(int(limit or 20), 100))
    candidates = await (
        db[AI_HUB_TURNS_COLLECTION]
        .find(
            {
                "bot_name": bot_name,
                "$or": [
                    {
                        "status": {"$in": ["queued", "interrupted"]},
                        "attempts": {"$lt": _MAX_TURN_ATTEMPTS},
                    },
                    {"status": "response_ready"},
                    {
                        "status": "running",
                        "lease_owner": {"$ne": worker_id},
                        "attempts": {"$lt": _MAX_TURN_ATTEMPTS},
                    },
                ],
            },
            {"turn_id": 1, "_id": 0},
        )
        .sort("created_at", 1)
        .limit(bounded)
        .to_list(bounded)
    )
    claimed: list[dict[str, Any]] = []
    for candidate in candidates:
        item = await claim_external_turn(
            db,
            turn_id=str(candidate.get("turn_id") or ""),
            worker_id=worker_id,
            recovery=True,
        )
        if item:
            claimed.append(item)
    return claimed


async def mark_external_turn_response_ready(
    db: AsyncIOMotorDatabase,
    *,
    turn_id: str,
    worker_id: str,
    final_text: str,
    artifacts: list[dict[str, Any]],
) -> bool:
    now = _now_datetime()
    result = await db[AI_HUB_TURNS_COLLECTION].update_one(
        {"turn_id": turn_id, "status": "running", "lease_owner": worker_id},
        {
            "$set": {
                "status": "response_ready",
                "final_text": str(final_text or "")[:100_000],
                "artifacts": list(artifacts or [])[:50],
                "response_ready_at": now,
                "updated_at": now,
            }
        },
    )
    return bool(result.modified_count)


async def mark_external_turn_completed(
    db: AsyncIOMotorDatabase,
    *,
    turn_id: str,
    worker_id: str,
) -> bool:
    now = _now_datetime()
    result = await db[AI_HUB_TURNS_COLLECTION].update_one(
        {
            "turn_id": turn_id,
            "lease_owner": worker_id,
            "status": {"$in": ["running", "response_ready"]},
        },
        {
            "$set": {
                "status": "completed",
                "completed_at": now,
                "updated_at": now,
                "expires_at": datetime.fromtimestamp(
                    now.timestamp() + _TURN_RETAIN_SECONDS,
                    tz=timezone.utc,
                ),
            }
        },
    )
    return bool(result.modified_count)


async def mark_external_turn_interrupted(
    db: AsyncIOMotorDatabase,
    *,
    turn_id: str,
    worker_id: str,
    error: str = "",
    preserve_response: bool = False,
) -> bool:
    status = "response_ready" if preserve_response else "interrupted"
    result = await db[AI_HUB_TURNS_COLLECTION].update_one(
        {
            "turn_id": turn_id,
            "lease_owner": worker_id,
            "status": {"$in": ["running", "response_ready"]},
        },
        {
            "$set": {
                "status": status,
                "last_error": str(error or "")[:2_000],
                "updated_at": _now_datetime(),
            }
        },
    )
    return bool(result.modified_count)


async def mark_external_turn_failed(
    db: AsyncIOMotorDatabase,
    *,
    turn_id: str,
    worker_id: str,
    error: str,
) -> bool:
    now = _now_datetime()
    result = await db[AI_HUB_TURNS_COLLECTION].update_one(
        {
            "turn_id": turn_id,
            "lease_owner": worker_id,
            "status": {"$in": ["running", "response_ready", "interrupted"]},
        },
        {
            "$set": {
                "status": "failed",
                "last_error": str(error or "")[:2_000],
                "completed_at": now,
                "updated_at": now,
                "expires_at": datetime.fromtimestamp(
                    now.timestamp() + _TURN_RETAIN_SECONDS,
                    tz=timezone.utc,
                ),
            }
        },
    )
    return bool(result.modified_count)


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
