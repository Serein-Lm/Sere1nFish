"""声音复刻 DAO — 音色记录与合成历史的持久化层。

集合:
  - voice_clones:            本地音色记录（与 DashScope 同步）
  - voice_synthesis_records:  合成历史（文本、状态、音频大小、耗时等）
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

CLONES_COLLECTION = "voice_clones"
SYNTHESIS_COLLECTION = "voice_synthesis_records"


# ==================== 索引 ====================

async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    clones = db[CLONES_COLLECTION]
    await clones.create_index("voice_id", unique=True)
    await clones.create_index("status")
    await clones.create_index("created_at")

    synth = db[SYNTHESIS_COLLECTION]
    await synth.create_index("record_id", unique=True)
    await synth.create_index("voice_id")
    await synth.create_index("status")
    await synth.create_index("created_at")
    await synth.create_index([("voice_id", 1), ("created_at", -1)])


# ==================== 音色记录 ====================

async def save_clone(
    db: AsyncIOMotorDatabase,
    *,
    voice_id: str,
    model: str,
    prefix: str,
    url: str,
    language_hints: list[str] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    doc = {
        "voice_id": voice_id,
        "model": model,
        "prefix": prefix,
        "url": url,
        "language_hints": language_hints or [],
        "status": "active",
        "request_id": request_id,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    await db[CLONES_COLLECTION].update_one(
        {"voice_id": voice_id}, {"$set": doc}, upsert=True,
    )
    return doc


async def list_clones(
    db: AsyncIOMotorDatabase,
    *,
    prefix: str | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    q: dict[str, Any] = {}
    if prefix:
        q["prefix"] = prefix
    if status:
        q["status"] = status
    coll = db[CLONES_COLLECTION]
    total = await coll.count_documents(q)
    cursor = coll.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    items = await cursor.to_list(limit)
    return items, total


async def get_clone(db: AsyncIOMotorDatabase, voice_id: str) -> dict[str, Any] | None:
    return await db[CLONES_COLLECTION].find_one({"voice_id": voice_id}, {"_id": 0})


async def update_clone_status(
    db: AsyncIOMotorDatabase, voice_id: str, status: str,
) -> bool:
    r = await db[CLONES_COLLECTION].update_one(
        {"voice_id": voice_id},
        {"$set": {"status": status, "updated_at": time.time()}},
    )
    return r.modified_count > 0


async def delete_clone(db: AsyncIOMotorDatabase, voice_id: str) -> bool:
    r = await db[CLONES_COLLECTION].delete_one({"voice_id": voice_id})
    return r.deleted_count > 0


# ==================== 合成记录 ====================

async def create_synthesis_record(
    db: AsyncIOMotorDatabase,
    *,
    voice_id: str,
    text: str,
    model: str,
) -> str:
    record_id = f"syn-{uuid.uuid4().hex[:12]}"
    doc = {
        "record_id": record_id,
        "voice_id": voice_id,
        "text": text,
        "text_length": len(text),
        "model": model,
        "status": "processing",
        "audio_bytes": 0,
        "first_pkg_delay_ms": 0,
        "request_id": None,
        "error": None,
        "created_at": time.time(),
        "completed_at": None,
    }
    await db[SYNTHESIS_COLLECTION].insert_one(doc)
    return record_id


async def complete_synthesis_record(
    db: AsyncIOMotorDatabase,
    record_id: str,
    *,
    audio_bytes: int,
    first_pkg_delay_ms: int = 0,
    request_id: str | None = None,
) -> None:
    await db[SYNTHESIS_COLLECTION].update_one(
        {"record_id": record_id},
        {"$set": {
            "status": "completed",
            "audio_bytes": audio_bytes,
            "first_pkg_delay_ms": first_pkg_delay_ms,
            "request_id": request_id,
            "completed_at": time.time(),
        }},
    )


async def fail_synthesis_record(
    db: AsyncIOMotorDatabase,
    record_id: str,
    error: str,
) -> None:
    await db[SYNTHESIS_COLLECTION].update_one(
        {"record_id": record_id},
        {"$set": {
            "status": "failed",
            "error": error,
            "completed_at": time.time(),
        }},
    )


async def list_synthesis_records(
    db: AsyncIOMotorDatabase,
    *,
    voice_id: str | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    q: dict[str, Any] = {}
    if voice_id:
        q["voice_id"] = voice_id
    if status:
        q["status"] = status
    coll = db[SYNTHESIS_COLLECTION]
    total = await coll.count_documents(q)
    cursor = coll.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    items = await cursor.to_list(limit)
    return items, total


async def get_synthesis_record(
    db: AsyncIOMotorDatabase, record_id: str,
) -> dict[str, Any] | None:
    return await db[SYNTHESIS_COLLECTION].find_one(
        {"record_id": record_id}, {"_id": 0},
    )
