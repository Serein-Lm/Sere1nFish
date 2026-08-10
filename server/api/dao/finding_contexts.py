"""Finding 上下文整理结果与可恢复执行队列 DAO。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import FINDING_CONTEXTS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def context_id_for_finding(finding_id: str) -> str:
    value = str(finding_id or "").strip()
    if not value:
        raise ValueError("finding_id 不能为空")
    digest = hashlib.sha256(f"finding-context:{value}".encode("utf-8")).hexdigest()
    return "fctx_" + digest[:24]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[FINDING_CONTEXTS_COLLECTION]
    await collection.create_index("context_id", unique=True)
    await collection.create_index("finding_id", unique=True)
    await collection.create_index(
        [("status", 1), ("priority", -1), ("queued_at", 1)]
    )
    await collection.create_index(
        [("project_id", 1), ("target_id", 1), ("updated_at", -1)]
    )
    await collection.create_index("source_document_ids")


async def get_by_finding_id(
    db: AsyncIOMotorDatabase,
    finding_id: str,
) -> dict[str, Any] | None:
    return await db[FINDING_CONTEXTS_COLLECTION].find_one(
        {"finding_id": str(finding_id or "").strip()},
        {"_id": 0},
    )


async def get_by_context_id(
    db: AsyncIOMotorDatabase,
    context_id: str,
) -> dict[str, Any] | None:
    return await db[FINDING_CONTEXTS_COLLECTION].find_one(
        {"context_id": context_id},
        {"_id": 0},
    )


async def queue_context(
    db: AsyncIOMotorDatabase,
    *,
    finding_id: str,
    project_id: str,
    target_id: str,
    source: str,
    source_url: str,
    source_document_ids: list[str],
    source_document_version_ids: list[str],
    input_fingerprint: str,
    prompt_slug: str,
    prompt_hash: str,
    priority: int,
    force: bool = False,
) -> dict[str, Any]:
    """幂等创建派生任务；输入变化时自动失效旧结果。"""
    normalized_finding_id = str(finding_id or "").strip()
    context_id = context_id_for_finding(normalized_finding_id)
    collection = db[FINDING_CONTEXTS_COLLECTION]
    now = _now()
    existing = await collection.find_one(
        {"finding_id": normalized_finding_id},
        {"_id": 0},
    )
    same_input = bool(
        existing and existing.get("input_fingerprint") == input_fingerprint
    )
    if existing and existing.get("status") == "running":
        if force or not same_input:
            await collection.update_one(
                {"context_id": context_id},
                {
                    "$set": {
                        "rerun_requested": True,
                        "requested_fingerprint": input_fingerprint,
                        "updated_at": now,
                    }
                },
            )
        return await get_by_context_id(db, context_id) or existing
    if existing and same_input and existing.get("status") in {
        "pending",
        "completed",
    } and not force:
        return existing

    fields: dict[str, Any] = {
        "context_id": context_id,
        "finding_id": normalized_finding_id,
        "project_id": project_id,
        "target_id": target_id,
        "source": source,
        "source_url": source_url,
        "source_document_ids": list(dict.fromkeys(source_document_ids)),
        "source_document_version_ids": list(
            dict.fromkeys(source_document_version_ids)
        ),
        "input_fingerprint": input_fingerprint,
        "prompt_slug": prompt_slug,
        "prompt_hash": prompt_hash,
        "priority": max(0, min(int(priority or 0), 100)),
        "status": "pending",
        "error": "",
        "queued_at": now,
        "updated_at": now,
        "rerun_requested": False,
        "attempt_count": 0,
    }
    await collection.update_one(
        {"context_id": context_id},
        {
            "$set": fields,
            "$setOnInsert": {"created_at": now},
            "$unset": {
                "started_at": "",
                "completed_at": "",
                "model": "",
            },
        },
        upsert=True,
    )
    return await get_by_context_id(db, context_id) or fields


async def claim_next_pending(
    db: AsyncIOMotorDatabase,
) -> dict[str, Any] | None:
    now = _now()
    return await db[FINDING_CONTEXTS_COLLECTION].find_one_and_update(
        {"status": "pending"},
        {
            "$set": {
                "status": "running",
                "started_at": now,
                "updated_at": now,
                "error": "",
            },
            "$inc": {"attempt_count": 1},
        },
        sort=[("priority", -1), ("queued_at", 1)],
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def mark_completed(
    db: AsyncIOMotorDatabase,
    *,
    context_id: str,
    input_fingerprint: str,
    result: dict[str, Any],
    model: str,
    evidence_manifest: dict[str, Any],
) -> dict[str, Any] | None:
    """完成当前轮次；运行期间收到强制刷新时立即回到待处理。"""
    collection = db[FINDING_CONTEXTS_COLLECTION]
    current = await collection.find_one(
        {"context_id": context_id},
        {"_id": 0, "rerun_requested": 1, "requested_fingerprint": 1},
    )
    rerun = bool((current or {}).get("rerun_requested"))
    now = _now()
    status = "pending" if rerun else "completed"
    fields: dict[str, Any] = {
        "status": status,
        "result": result,
        "model": model,
        "evidence_manifest": evidence_manifest,
        "input_fingerprint": (
            str((current or {}).get("requested_fingerprint") or input_fingerprint)
            if rerun
            else input_fingerprint
        ),
        "error": "",
        "updated_at": now,
        "completed_at": now,
        "rerun_requested": False,
    }
    if rerun:
        fields["queued_at"] = now
    update: dict[str, Any] = {
        "$set": fields,
        "$unset": {"requested_fingerprint": ""},
    }
    return await collection.find_one_and_update(
        {"context_id": context_id},
        update,
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def mark_error(
    db: AsyncIOMotorDatabase,
    *,
    context_id: str,
    error: str,
    retry: bool,
) -> dict[str, Any] | None:
    now = _now()
    fields: dict[str, Any] = {
        "status": "pending" if retry else "error",
        "error": str(error or "")[:2_000],
        "updated_at": now,
    }
    if retry:
        fields["queued_at"] = now
    return await db[FINDING_CONTEXTS_COLLECTION].find_one_and_update(
        {"context_id": context_id},
        {"$set": fields},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def recover_interrupted(db: AsyncIOMotorDatabase) -> int:
    result = await db[FINDING_CONTEXTS_COLLECTION].update_many(
        {"status": "running"},
        {
            "$set": {
                "status": "pending",
                "error": "进程重载中断，已重新进入整理队列",
                "queued_at": _now(),
                "updated_at": _now(),
            }
        },
    )
    return int(result.modified_count or 0)
