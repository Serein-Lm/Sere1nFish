"""Atomic lease persistence for distributed scan work items."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db.collections import (
    DISTRIBUTED_WORK_EVENTS_COLLECTION,
    DISTRIBUTED_WORK_ITEMS_COLLECTION,
)


ACTIVE_STATUSES = ("leased", "running")
READY_STATUSES = ("queued", "retry")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public(doc: dict[str, Any] | None, *, detail: bool = True) -> dict[str, Any] | None:
    if not doc:
        return None
    result = dict(doc)
    result.pop("_id", None)
    lease = result.get("lease")
    if isinstance(lease, dict):
        result["lease"] = {key: value for key, value in lease.items() if key != "token_digest"}
    if not detail:
        result.pop("payload", None)
        result.pop("result", None)
        result.pop("artifacts", None)
    return result


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    work = db[DISTRIBUTED_WORK_ITEMS_COLLECTION]
    await work.create_index("work_item_id", unique=True)
    await work.create_index("idempotency_key", unique=True)
    await work.create_index(
        [("status", ASCENDING), ("available_at", ASCENDING), ("priority", DESCENDING)]
    )
    await work.create_index("lease.expires_at")
    await work.create_index([("task_id", ASCENDING), ("status", ASCENDING)])
    await work.create_index([("project_id", ASCENDING), ("created_at", DESCENDING)])
    await work.create_index([("lease.node_id", ASCENDING), ("status", ASCENDING)])

    events = db[DISTRIBUTED_WORK_EVENTS_COLLECTION]
    await events.create_index([("work_item_id", ASCENDING), ("event_seq", ASCENDING)], unique=True)
    await events.create_index([("task_id", ASCENDING), ("created_at", DESCENDING)])
    await events.create_index("created_at", expireAfterSeconds=30 * 24 * 3600)


async def enqueue(
    db: AsyncIOMotorDatabase,
    document: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    now = _now()
    doc = {
        **document,
        "status": "queued",
        "attempt": 0,
        "last_event_seq": 0,
        "available_at": document.get("available_at") or now,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].insert_one(doc)
        return _public(doc) or {}, True
    except DuplicateKeyError:
        existing = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one(
            {"idempotency_key": document["idempotency_key"]},
            {"_id": 0},
        )
        if not existing:
            raise
        return _public(existing) or {}, False


async def get_work(
    db: AsyncIOMotorDatabase,
    work_item_id: str,
    *,
    detail: bool = True,
) -> dict[str, Any] | None:
    doc = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one(
        {"work_item_id": work_item_id},
        {"_id": 0},
    )
    return _public(doc, detail=detail)


async def list_work(
    db: AsyncIOMotorDatabase,
    *,
    status: str = "",
    kind: str = "",
    project_id: str = "",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    if kind:
        query["kind"] = kind
    if project_id:
        query["project_id"] = project_id
    collection = db[DISTRIBUTED_WORK_ITEMS_COLLECTION]
    total = await collection.count_documents(query)
    cursor = (
        collection.find(query, {"_id": 0, "payload": 0, "result": 0, "artifacts": 0, "lease.token_digest": 0})
        .sort([("created_at", DESCENDING), ("priority", DESCENDING)])
        .skip(max(0, skip))
        .limit(max(1, min(limit, 200)))
    )
    return [doc async for doc in cursor], total


def _requirements_satisfied(doc: dict[str, Any], capabilities: set[str]) -> bool:
    required = set((doc.get("requirements") or {}).get("capabilities") or [])
    return required.issubset(capabilities)


async def lease_next(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    capabilities: set[str],
    token_digest: str,
    lease_seconds: int,
    kinds: list[str] | None = None,
) -> dict[str, Any] | None:
    now = _now()
    query: dict[str, Any] = {
        "status": {"$in": list(READY_STATUSES)},
        "available_at": {"$lte": now},
        "kind": {"$in": sorted(capabilities)},
        "$expr": {
            "$lt": [
                {"$ifNull": ["$attempt", 0]},
                {"$ifNull": ["$max_attempts", 3]},
            ]
        },
    }
    allowed_kinds = sorted(set(kinds or []).intersection(capabilities))
    if kinds:
        if not allowed_kinds:
            return None
        query["kind"] = {"$in": allowed_kinds}
    candidates = await (
        db[DISTRIBUTED_WORK_ITEMS_COLLECTION]
        .find(query, {"_id": 0})
        .sort([("priority", DESCENDING), ("available_at", ASCENDING), ("created_at", ASCENDING)])
        .limit(50)
        .to_list(length=50)
    )
    for candidate in candidates:
        if not _requirements_satisfied(candidate, capabilities):
            continue
        work_item_id = str(candidate.get("work_item_id") or "")
        expires_at = now + timedelta(seconds=max(30, min(lease_seconds, 300)))
        doc = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one_and_update(
            {
                "work_item_id": work_item_id,
                "status": {"$in": list(READY_STATUSES)},
                "available_at": {"$lte": now},
                "$expr": {
                    "$lt": [
                        {"$ifNull": ["$attempt", 0]},
                        {"$ifNull": ["$max_attempts", 3]},
                    ]
                },
            },
            {
                "$set": {
                    "status": "leased",
                    "lease": {
                        "node_id": node_id,
                        "token_digest": token_digest,
                        "leased_at": now,
                        "expires_at": expires_at,
                    },
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
                "$unset": {"last_error": "", "completed_at": "", "failed_at": ""},
            },
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
        if doc:
            return _public(doc)
    return None


def _active_lease_query(
    *,
    work_item_id: str,
    node_id: str,
    token_digest: str,
) -> dict[str, Any]:
    return {
        "work_item_id": work_item_id,
        "status": {"$in": list(ACTIVE_STATUSES)},
        "lease.node_id": node_id,
        "lease.token_digest": token_digest,
        "lease.expires_at": {"$gt": _now()},
    }


async def mark_started(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    node_id: str,
    token_digest: str,
    event_seq: int,
) -> dict[str, Any] | None:
    now = _now()
    doc = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one_and_update(
        {
            **_active_lease_query(
                work_item_id=work_item_id,
                node_id=node_id,
                token_digest=token_digest,
            ),
            "last_event_seq": {"$lt": event_seq},
        },
        {
            "$set": {
                "status": "running",
                "started_at": now,
                "last_event_seq": event_seq,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return _public(doc)


async def renew_lease(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    node_id: str,
    token_digest: str,
    lease_seconds: int,
) -> bool:
    result = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].update_one(
        _active_lease_query(
            work_item_id=work_item_id,
            node_id=node_id,
            token_digest=token_digest,
        ),
        {
            "$set": {
                "lease.expires_at": _now() + timedelta(seconds=max(30, min(lease_seconds, 300))),
                "updated_at": _now(),
            }
        },
    )
    return bool(result.modified_count)


async def record_progress(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    node_id: str,
    token_digest: str,
    event_seq: int,
    progress: dict[str, Any],
) -> dict[str, Any] | None:
    doc = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one_and_update(
        {
            **_active_lease_query(
                work_item_id=work_item_id,
                node_id=node_id,
                token_digest=token_digest,
            ),
            "last_event_seq": {"$lt": event_seq},
        },
        {
            "$set": {
                "progress": progress,
                "last_event_seq": event_seq,
                "updated_at": _now(),
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return _public(doc)


async def complete(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    node_id: str,
    token_digest: str,
    event_seq: int,
    result: dict[str, Any],
    artifacts: list[dict[str, Any]],
    completion_digest: str,
) -> tuple[dict[str, Any] | None, bool]:
    existing = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one(
        {"work_item_id": work_item_id},
        {"_id": 0},
    )
    if existing and existing.get("status") == "completed":
        same = (
            existing.get("completion_digest") == completion_digest
            and (existing.get("lease") or {}).get("node_id") == node_id
            and (existing.get("lease") or {}).get("token_digest") == token_digest
        )
        return (_public(existing) if same else None), same

    now = _now()
    doc = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one_and_update(
        {
            **_active_lease_query(
                work_item_id=work_item_id,
                node_id=node_id,
                token_digest=token_digest,
            ),
            "last_event_seq": {"$lt": event_seq},
        },
        {
            "$set": {
                "status": "completed",
                "result": result,
                "artifacts": artifacts,
                "completion_digest": completion_digest,
                "last_event_seq": event_seq,
                "completed_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return _public(doc), False


async def fail(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    node_id: str,
    token_digest: str,
    event_seq: int,
    error_code: str,
    error: str,
    retryable: bool,
    retry_delay_seconds: int,
) -> dict[str, Any] | None:
    current = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one(
        _active_lease_query(
            work_item_id=work_item_id,
            node_id=node_id,
            token_digest=token_digest,
        ),
        {"_id": 0},
    )
    if not current or int(current.get("last_event_seq") or 0) >= event_seq:
        return None
    should_retry = retryable and int(current.get("attempt") or 0) < int(current.get("max_attempts") or 3)
    now = _now()
    fields: dict[str, Any] = {
        "status": "retry" if should_retry else "failed",
        "last_error": {"code": error_code, "message": error[:1000]},
        "last_event_seq": event_seq,
        "updated_at": now,
    }
    if should_retry:
        fields["available_at"] = now + timedelta(seconds=max(0, min(retry_delay_seconds, 3600)))
    else:
        fields["failed_at"] = now
    update: dict[str, Any] = {"$set": fields}
    if should_retry:
        update["$unset"] = {"lease": ""}
    doc = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one_and_update(
        {
            **_active_lease_query(
                work_item_id=work_item_id,
                node_id=node_id,
                token_digest=token_digest,
            ),
            "last_event_seq": {"$lt": event_seq},
        },
        update,
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return _public(doc)


async def abandon_lease(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    node_id: str,
    reason: str,
    delay_seconds: int = 5,
) -> bool:
    now = _now()
    current = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one(
        {
            "work_item_id": work_item_id,
            "status": {"$in": list(ACTIVE_STATUSES)},
            "lease.node_id": node_id,
        },
        {"_id": 0, "attempt": 1, "max_attempts": 1},
    )
    if not current:
        return False
    exhausted = int(current.get("attempt") or 0) >= int(
        current.get("max_attempts") or 3
    )
    fields: dict[str, Any] = {
        "status": "failed" if exhausted else "retry",
        "last_error": {"code": "lease_abandoned", "message": reason[:1000]},
        "updated_at": now,
    }
    if exhausted:
        fields["failed_at"] = now
    else:
        fields["available_at"] = now + timedelta(seconds=max(0, delay_seconds))
    result = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].update_one(
        {
            "work_item_id": work_item_id,
            "status": {"$in": list(ACTIVE_STATUSES)},
            "lease.node_id": node_id,
            "attempt": current.get("attempt", 0),
        },
        {"$set": fields, "$unset": {"lease": ""}},
    )
    return bool(result.modified_count)


async def cancel(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    reason: str,
) -> dict[str, Any] | None:
    now = _now()
    doc = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find_one_and_update(
        {"work_item_id": work_item_id, "status": {"$nin": list(TERMINAL_STATUSES)}},
        {
            "$set": {
                "status": "cancelled",
                "cancel_reason": reason[:500],
                "cancelled_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return _public(doc)


async def cancelled_for_node(
    db: AsyncIOMotorDatabase,
    node_id: str,
    *,
    limit: int = 100,
) -> list[str]:
    cursor = db[DISTRIBUTED_WORK_ITEMS_COLLECTION].find(
        {"status": "cancelled", "lease.node_id": node_id},
        {"_id": 0, "work_item_id": 1},
    ).limit(max(1, min(limit, 500)))
    return [str(doc["work_item_id"]) async for doc in cursor]


async def record_event(
    db: AsyncIOMotorDatabase,
    *,
    work: dict[str, Any],
    node_id: str,
    event_seq: int,
    event_type: str,
    data: dict[str, Any] | None = None,
) -> bool:
    try:
        await db[DISTRIBUTED_WORK_EVENTS_COLLECTION].insert_one(
            {
                "work_item_id": work.get("work_item_id"),
                "task_id": work.get("task_id", ""),
                "project_id": work.get("project_id", ""),
                "target_id": work.get("target_id", ""),
                "node_id": node_id,
                "event_seq": event_seq,
                "event_type": event_type,
                "data": data or {},
                "created_at": _now(),
            }
        )
        return True
    except DuplicateKeyError:
        return False


async def requeue_expired(db: AsyncIOMotorDatabase, *, limit: int = 500) -> dict[str, int]:
    now = _now()
    cursor = (
        db[DISTRIBUTED_WORK_ITEMS_COLLECTION]
        .find(
            {"status": {"$in": list(ACTIVE_STATUSES)}, "lease.expires_at": {"$lte": now}},
            {"_id": 0, "work_item_id": 1, "attempt": 1, "max_attempts": 1},
        )
        .limit(max(1, min(limit, 2000)))
    )
    requeued = 0
    failed = 0
    async for item in cursor:
        exhausted = int(item.get("attempt") or 0) >= int(item.get("max_attempts") or 3)
        status = "failed" if exhausted else "retry"
        fields: dict[str, Any] = {
            "status": status,
            "last_error": {"code": "lease_expired", "message": "节点租约已过期"},
            "updated_at": now,
        }
        if exhausted:
            fields["failed_at"] = now
        else:
            fields["available_at"] = now + timedelta(seconds=5)
        update: dict[str, Any] = {"$set": fields}
        if not exhausted:
            update["$unset"] = {"lease": ""}
        result = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].update_one(
            {
                "work_item_id": item["work_item_id"],
                "status": {"$in": list(ACTIVE_STATUSES)},
                "lease.expires_at": {"$lte": now},
            },
            update,
        )
        if result.modified_count:
            failed += int(exhausted)
            requeued += int(not exhausted)
    return {"requeued": requeued, "failed": failed}


async def get_stats(db: AsyncIOMotorDatabase) -> dict[str, int]:
    rows = await db[DISTRIBUTED_WORK_ITEMS_COLLECTION].aggregate(
        [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    ).to_list(length=None)
    result: dict[str, int] = {"total": 0, "queued": 0, "leased": 0, "running": 0, "retry": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for row in rows:
        status = str(row.get("_id") or "queued")
        count = int(row.get("count") or 0)
        result["total"] += count
        result[status] = count
    result["active"] = result.get("leased", 0) + result.get("running", 0)
    return result
