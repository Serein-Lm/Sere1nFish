"""Atomic ownership of evidence-completion attempts, separate from readability."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from api.db.collections import SOURCE_DOCUMENT_VERSIONS_COLLECTION

ATTEMPT_LEASE_SECONDS = 120


class SourceVersionBusy(RuntimeError):
    """Another live attempt owns this content version."""


class SourceVersionOwnershipLost(RuntimeError):
    """A stale attempt must not replace a newer evidence snapshot."""


async def begin_version(
    db: AsyncIOMotorDatabase, *, version_id: str, document_id: str,
    content_hash: str, source_type: str, attempt_id: str = "",
    project_id: str = "", task_id: str = "",
) -> None:
    now = datetime.now(timezone.utc)
    attempt_id = attempt_id or uuid4().hex
    query = {"version_id": version_id, "$or": [
        {"archive_attempt.status": {"$ne": "processing"}},
        {"archive_attempt.lease_expires_at": {"$lte": now}},
        {"archive_attempt.id": attempt_id},
    ]}
    fields = {
        "version_id": {"$literal": version_id},
        "document_id": {"$ifNull": ["$document_id", {"$literal": document_id}]},
        "content_hash": {"$ifNull": ["$content_hash", {"$literal": content_hash}]},
        "source_type": {"$ifNull": ["$source_type", {"$literal": source_type}]},
        "status": {"$cond": [{"$eq": ["$status", "ready"]}, "ready", "processing"]},
        "created_at": {"$ifNull": ["$created_at", now]},
        "captured_at": {"$ifNull": ["$captured_at", now]},
        "updated_at": now,
        "archive_attempt": {"$literal": {
            "id": attempt_id, "status": "processing", "started_at": now,
            "heartbeat_at": now, "lease_expires_at": now + timedelta(seconds=ATTEMPT_LEASE_SECONDS),
            "project_id": project_id, "task_id": task_id,
        }},
    }
    try:
        await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].update_one(query, [{"$set": fields}], upsert=True)
    except DuplicateKeyError as exc:
        raise SourceVersionBusy(f"来源版本正在补齐证据: {version_id}") from exc


def _owned(version_id: str, attempt_id: str) -> dict[str, Any]:
    query: dict[str, Any] = {"version_id": version_id}
    if attempt_id:
        query.update({"archive_attempt.id": attempt_id, "archive_attempt.status": "processing"})
    return query


async def renew_version_attempt(db: AsyncIOMotorDatabase, *, version_id: str, attempt_id: str) -> bool:
    now = datetime.now(timezone.utc)
    result = await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].update_one(
        _owned(version_id, attempt_id), {"$set": {
            "archive_attempt.heartbeat_at": now,
            "archive_attempt.lease_expires_at": now + timedelta(seconds=ATTEMPT_LEASE_SECONDS),
        }},
    )
    return bool(result.matched_count)


async def mark_version_ready(
    db: AsyncIOMotorDatabase, *, version_id: str, payload: dict[str, Any], attempt_id: str = "",
    expected_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    fields = {key: value for key, value in payload.items() if key not in {"_id", "archive_attempt"}}
    fields.update({"status": "ready", "error": "", "updated_at": now})
    if attempt_id:
        fields.update({"archive_attempt.status": "completed", "archive_attempt.finished_at": now,
                       "archive_attempt.lease_expires_at": now})
    query = _owned(version_id, attempt_id)
    if expected_snapshot is not None:
        query.update({"status": "ready", "updated_at": expected_snapshot.get("updated_at"),
                      "archive_attempt.status": {"$ne": "processing"}})
    checked = bool(attempt_id) or expected_snapshot is not None
    result = await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].update_one(
        query, {"$set": fields}, upsert=not checked,
    )
    if checked and not result.matched_count:
        raise SourceVersionOwnershipLost(f"来源归档拥有者已失效: {version_id}")
    return await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].find_one({"version_id": version_id}, {"_id": 0}) or payload


async def mark_version_error(
    db: AsyncIOMotorDatabase, version_id: str, error: str, *,
    attempt_id: str = "", interrupted: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    message = str(error or "来源归档被中断")[:2000]
    fields: dict[str, Any] = {
        "status": {"$cond": [{"$eq": ["$status", "ready"]}, "ready", "error"]},
        "error": {"$cond": [{"$eq": ["$status", "ready"]}, {"$ifNull": ["$error", ""]}, {"$literal": message}]},
        "updated_at": now,
    }
    if attempt_id:
        fields.update({"archive_attempt.status": "interrupted" if interrupted else "error",
                       "archive_attempt.error": {"$literal": message},
                       "archive_attempt.finished_at": now, "archive_attempt.lease_expires_at": now})
    await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].update_one(_owned(version_id, attempt_id), [{"$set": fields}])


async def expire_abandoned_attempts(db: AsyncIOMotorDatabase) -> int:
    """Close expired durable leases after hard termination, preserving ready data."""
    now = datetime.now(timezone.utc)
    message = "来源归档执行租约已过期，保留证据等待按需恢复"
    result = await db[SOURCE_DOCUMENT_VERSIONS_COLLECTION].update_many(
        {"archive_attempt.status": "processing", "archive_attempt.lease_expires_at": {"$lte": now}},
        [{"$set": {
            "status": {"$cond": [{"$eq": ["$status", "ready"]}, "ready", "error"]},
            "error": {"$cond": [{"$eq": ["$status", "ready"]}, {"$ifNull": ["$error", ""]}, message]},
            "archive_attempt.status": "interrupted", "archive_attempt.error": message,
            "archive_attempt.finished_at": now, "updated_at": now,
        }}],
    )
    return int(result.modified_count)
