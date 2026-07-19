"""Project task read access used by API and AI data adapters."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import TASKS_COLLECTION


MAX_AUTOMATIC_RECOVERIES = 3
_BATCH_NOTIFICATION_CLAIM_SECONDS = 10 * 60
_TERMINAL_TASK_STATUSES = {"completed", "error", "failed", "cancelled"}


async def insert_tasks(
    db: AsyncIOMotorDatabase,
    documents: list[dict[str, Any]],
) -> int:
    """Insert project task documents as one atomic batch request."""
    if not documents:
        return 0
    result = await db[TASKS_COLLECTION].insert_many(documents)
    return len(result.inserted_ids)


async def prepare_interrupted_tasks(
    db: AsyncIOMotorDatabase,
) -> tuple[list[dict[str, Any]], int]:
    """Return unfinished tasks to the persistent queue after a process restart."""
    now = datetime.now(timezone.utc)
    unfinished = await db[TASKS_COLLECTION].find(
        {"status": {"$in": ["pending", "running"]}},
        {"_id": 0},
    ).to_list(None)
    pending = [
        item
        for item in unfinished
        if str(item.get("status") or "") == "pending"
    ]
    interrupted = [
        item
        for item in unfinished
        if str(item.get("status") or "") == "running"
    ]
    recoverable_running = [
        item
        for item in interrupted
        if int(item.get("recovery_count") or 0) < MAX_AUTOMATIC_RECOVERIES
    ]
    exhausted = [
        item
        for item in interrupted
        if int(item.get("recovery_count") or 0) >= MAX_AUTOMATIC_RECOVERIES
    ]
    recoverable = [*pending, *recoverable_running]

    pending_ids = [str(item.get("task_id") or "") for item in pending]
    if pending_ids:
        await db[TASKS_COLLECTION].update_many(
            {"task_id": {"$in": pending_ids}, "status": "pending"},
            {
                "$set": {
                    "progress": {
                        "stage": "recovering",
                        "message": "服务已启动，排队任务等待重新认领",
                    },
                    "last_requeued_at": now,
                    "updated_at": now,
                },
                "$unset": {
                    "runtime_id": "",
                    "heartbeat_at": "",
                    "completed_at": "",
                    "error": "",
                },
            },
        )

    running_ids = [str(item.get("task_id") or "") for item in recoverable_running]
    if running_ids:
        await db[TASKS_COLLECTION].update_many(
            {"task_id": {"$in": running_ids}, "status": "running"},
            {
                "$set": {
                    "status": "pending",
                    "progress": {
                        "stage": "recovering",
                        "message": "服务已恢复，中断任务等待重新认领",
                    },
                    "last_recovered_at": now,
                    "updated_at": now,
                },
                "$inc": {"recovery_count": 1},
                "$unset": {
                    "runtime_id": "",
                    "heartbeat_at": "",
                    "completed_at": "",
                    "error": "",
                },
            },
        )
        for item in recoverable_running:
            item["status"] = "pending"
            item["recovery_count"] = int(item.get("recovery_count") or 0) + 1
            item["last_recovered_at"] = now

    exhausted_ids = [str(item.get("task_id") or "") for item in exhausted]
    if exhausted_ids:
        reason = f"任务连续恢复 {MAX_AUTOMATIC_RECOVERIES} 次仍未完成，已停止自动重试"
        exhausted_result = await db[TASKS_COLLECTION].update_many(
            {"task_id": {"$in": exhausted_ids}, "status": "running"},
            {
                "$set": {
                    "status": "error",
                    "error": reason,
                    "progress": {"stage": "error", "message": reason},
                    "updated_at": now,
                    "completed_at": now,
                },
                "$unset": {"runtime_id": "", "heartbeat_at": ""},
            },
        )
        exhausted_count = int(exhausted_result.modified_count)
    else:
        exhausted_count = 0
    return recoverable, exhausted_count


async def mark_tasks_unrecoverable(
    db: AsyncIOMotorDatabase,
    task_ids: list[str],
    *,
    reason: str,
) -> int:
    if not task_ids:
        return 0
    now = datetime.now(timezone.utc)
    result = await db[TASKS_COLLECTION].update_many(
        {"task_id": {"$in": task_ids}, "status": "pending"},
        {
            "$set": {
                "status": "error",
                "error": reason,
                "progress": {"stage": "error", "message": reason},
                "updated_at": now,
                "completed_at": now,
            }
        },
    )
    return int(result.modified_count)


async def claim_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    runtime_id: str,
) -> dict[str, Any] | None:
    """Atomically claim one pending task for the current server process."""
    now = datetime.now(timezone.utc)
    return await db[TASKS_COLLECTION].find_one_and_update(
        {"task_id": task_id, "status": "pending"},
        {
            "$set": {
                "status": "running",
                "runtime_id": runtime_id,
                "started_at": now,
                "heartbeat_at": now,
                "updated_at": now,
            },
            "$inc": {"attempt_count": 1},
            "$unset": {"completed_at": "", "error": ""},
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def heartbeat_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    runtime_id: str,
) -> bool:
    result = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id, "status": "running", "runtime_id": runtime_id},
        {"$set": {"heartbeat_at": datetime.now(timezone.utc)}},
    )
    return bool(result.modified_count)


async def complete_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    runtime_id: str,
    elapsed_ms: int,
    result: Any = None,
) -> bool:
    now = datetime.now(timezone.utc)
    fields: dict[str, Any] = {
        "status": "completed",
        "elapsed_ms": elapsed_ms,
        "updated_at": now,
        "completed_at": now,
        "heartbeat_at": now,
    }
    if result is not None:
        fields["result"] = result
    updated = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id, "status": "running", "runtime_id": runtime_id},
        {"$set": fields, "$unset": {"runtime_id": ""}},
    )
    return bool(updated.modified_count)


async def fail_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    runtime_id: str,
    elapsed_ms: int,
    error: str,
) -> bool:
    now = datetime.now(timezone.utc)
    updated = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id, "status": "running", "runtime_id": runtime_id},
        {
            "$set": {
                "status": "error",
                "error": error,
                "elapsed_ms": elapsed_ms,
                "updated_at": now,
                "completed_at": now,
                "heartbeat_at": now,
            },
            "$unset": {"runtime_id": ""},
        },
    )
    return bool(updated.modified_count)


async def release_interrupted_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    runtime_id: str,
    reason: str,
) -> bool:
    now = datetime.now(timezone.utc)
    updated = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id, "status": "running", "runtime_id": runtime_id},
        {
            "$set": {
                "status": "pending",
                "progress": {"stage": "recovering", "message": reason},
                "updated_at": now,
            },
            "$unset": {"runtime_id": "", "heartbeat_at": ""},
        },
    )
    return bool(updated.modified_count)


async def claim_completed_batch_notification(
    db: AsyncIOMotorDatabase,
    *,
    batch_id: str,
) -> tuple[list[dict[str, Any]], str, str] | None:
    """Claim one aggregate notification after every task in a batch is terminal."""
    if not batch_id:
        return None
    projection = {
        "_id": 0,
        "task_id": 1,
        "batch_id": 1,
        "batch_index": 1,
        "batch_total": 1,
        "status": 1,
        "error": 1,
        "params": 1,
        "result.company_name": 1,
        "result.identity.normalized_name": 1,
        "result.assets.alive": 1,
        "result.url_scan.findings_count": 1,
        "result.bidding.records_fetched": 1,
        "result.bidding.findings_count": 1,
        "result.wechat.documents": 1,
        "result.wechat.contacts": 1,
        "result.scholar.articles_total": 1,
        "result.scholar.verified_articles_total": 1,
        "result.scholar.contacts_total": 1,
        "result.xhs.notes_count": 1,
        "result.xhs.profiles_count": 1,
    }
    documents = await (
        db[TASKS_COLLECTION]
        .find({"batch_id": batch_id}, projection)
        .sort("batch_index", 1)
        .to_list(None)
    )
    if not documents:
        return None
    expected_total = max(_count_int(item.get("batch_total")) for item in documents)
    if expected_total and len(documents) < expected_total:
        return None
    if any(str(item.get("status") or "") not in _TERMINAL_TASK_STATUSES for item in documents):
        return None

    owner = min(documents, key=lambda item: _count_int(item.get("batch_index")))
    owner_task_id = str(owner.get("task_id") or "")
    if not owner_task_id:
        return None
    now = datetime.now(timezone.utc)
    claim_token = uuid.uuid4().hex
    claimed = await db[TASKS_COLLECTION].find_one_and_update(
        {
            "task_id": owner_task_id,
            "batch_notification_sent_at": {"$exists": False},
            "$or": [
                {"batch_notification_claimed_at": {"$exists": False}},
                {
                    "batch_notification_claimed_at": {
                        "$lt": now - timedelta(seconds=_BATCH_NOTIFICATION_CLAIM_SECONDS)
                    }
                },
            ],
        },
        {
            "$set": {
                "batch_notification_claimed_at": now,
                "batch_notification_claim_token": claim_token,
            }
        },
        projection={"task_id": 1},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        return None
    return documents, owner_task_id, claim_token


async def complete_batch_notification_claim(
    db: AsyncIOMotorDatabase,
    *,
    owner_task_id: str,
    claim_token: str,
) -> bool:
    now = datetime.now(timezone.utc)
    result = await db[TASKS_COLLECTION].update_one(
        {
            "task_id": owner_task_id,
            "batch_notification_claim_token": claim_token,
        },
        {
            "$set": {"batch_notification_sent_at": now},
            "$unset": {
                "batch_notification_claimed_at": "",
                "batch_notification_claim_token": "",
            },
        },
    )
    return bool(result.modified_count)


async def release_batch_notification_claim(
    db: AsyncIOMotorDatabase,
    *,
    owner_task_id: str,
    claim_token: str,
) -> bool:
    result = await db[TASKS_COLLECTION].update_one(
        {
            "task_id": owner_task_id,
            "batch_notification_claim_token": claim_token,
        },
        {
            "$unset": {
                "batch_notification_claimed_at": "",
                "batch_notification_claim_token": "",
            }
        },
    )
    return bool(result.modified_count)


def _count_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


async def claim_stalled_task_alerts(
    db: AsyncIOMotorDatabase,
    *,
    stale_after_seconds: int,
    alert_cooldown_seconds: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Claim stale progress alerts without treating resource-queue waits as failures."""
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=max(60, stale_after_seconds))
    cooldown_before = now - timedelta(seconds=max(60, alert_cooldown_seconds))
    query = {
        "status": "running",
        "updated_at": {"$lt": stale_before},
        "progress.stage": {"$nin": ["waiting_mobile", "waiting_core"]},
        "$or": [
            {"last_stall_alert_at": {"$exists": False}},
            {"last_stall_alert_at": {"$lt": cooldown_before}},
        ],
    }
    candidates = await db[TASKS_COLLECTION].find(
        query,
        {"_id": 0},
    ).sort("updated_at", 1).limit(max(1, min(limit, 100))).to_list(None)
    claimed: list[dict[str, Any]] = []
    for item in candidates:
        task_id = str(item.get("task_id") or "")
        updated = await db[TASKS_COLLECTION].find_one_and_update(
            {**query, "task_id": task_id},
            {"$set": {"last_stall_alert_at": now}},
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
        if updated:
            claimed.append(updated)
    return claimed


async def list_tasks(
    db: AsyncIOMotorDatabase,
    project_id: str,
    *,
    task_type: str = "",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {"project_id": project_id}
    if task_type:
        query["task_type"] = task_type
    bounded_limit = max(1, min(int(limit or 50), 200))
    total = await db[TASKS_COLLECTION].count_documents(query)
    cursor = (
        db[TASKS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(max(0, int(skip or 0)))
        .limit(bounded_limit)
    )
    return await cursor.to_list(bounded_limit), total
