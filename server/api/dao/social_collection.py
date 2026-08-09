"""社交地点图片采集任务与媒体证据 DAO。"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from api.db.collections import (
    SOCIAL_COLLECTION_JOBS_COLLECTION,
    SOCIAL_MEDIA_EVIDENCE_COLLECTION,
)


ACTIVE_JOB_STATUSES = ("pending", "running", "paused")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    jobs = db[SOCIAL_COLLECTION_JOBS_COLLECTION]
    await jobs.create_index("job_id", unique=True)
    await jobs.create_index([("project_id", 1), ("created_at", -1)])
    await jobs.create_index([("status", 1), ("updated_at", -1)])
    await jobs.create_index("parent_task_id", sparse=True)
    await jobs.create_index(
        [("request_key", 1), ("status", 1), ("updated_at", -1)],
        sparse=True,
    )
    await jobs.create_index(
        [("project_id", 1), ("active_request_key", 1)],
        unique=True,
        partialFilterExpression={"active_request_key": {"$type": "string"}},
    )

    media = db[SOCIAL_MEDIA_EVIDENCE_COLLECTION]
    await media.create_index("evidence_id", unique=True)
    await media.create_index([("project_id", 1), ("created_at", -1)])
    await media.create_index([("job_ids", 1), ("created_at", -1)])
    await media.create_index([("record_ids", 1), ("created_at", -1)])
    await media.create_index([("target_ids", 1), ("created_at", -1)])
    await media.create_index([("platform", 1), ("place_name", 1)])
    await media.create_index("storage_object_id")
    await media.create_index("content_sha256")


async def find_active_job_by_request_key(
    db: AsyncIOMotorDatabase,
    request_key: str,
    *,
    project_id: str = "",
) -> dict[str, Any] | None:
    normalized = str(request_key or "").strip()
    if not normalized:
        return None
    query: dict[str, Any] = {
        "$or": [
            {"active_request_key": normalized},
            {
                "request_key": normalized,
                "status": {"$in": list(ACTIVE_JOB_STATUSES)},
            },
        ]
    }
    if project_id:
        query["project_id"] = project_id
    return await db[SOCIAL_COLLECTION_JOBS_COLLECTION].find_one(
        query,
        {"_id": 0},
        sort=[("created_at", -1)],
    )


async def create_job(
    db: AsyncIOMotorDatabase,
    *,
    job_id: str = "",
    payload: dict[str, Any],
    platform_tasks: list[dict[str, Any]],
    parent_task_id: str,
    requested_by: str,
) -> dict[str, Any]:
    job_id = str(job_id or "").strip() or "scj_" + uuid.uuid4().hex[:20]
    now = _now()
    doc = {
        "job_id": job_id,
        "parent_task_id": parent_task_id,
        "project_id": str(payload.get("project_id") or ""),
        "target_id": str(payload.get("target_id") or ""),
        "place_name": str(payload.get("place_name") or ""),
        "device_id": str(payload.get("device_id") or ""),
        "platforms": list(payload.get("platforms") or []),
        "keywords": list(payload.get("keywords") or []),
        "collection_goal": str(payload.get("collection_goal") or ""),
        "request_key": str(payload.get("request_key") or ""),
        "requested_by": requested_by,
        "status": "pending",
        "platform_tasks": platform_tasks,
        "progress": {
            "total": len(platform_tasks),
            "completed": 0,
            "failed": 0,
            "media_count": 0,
        },
        "created_at": now,
        "updated_at": now,
    }
    if doc["request_key"]:
        doc["active_request_key"] = doc["request_key"]
    await db[SOCIAL_COLLECTION_JOBS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def get_job(
    db: AsyncIOMotorDatabase,
    job_id: str,
) -> dict[str, Any] | None:
    return await db[SOCIAL_COLLECTION_JOBS_COLLECTION].find_one(
        {"job_id": str(job_id or "")},
        {"_id": 0},
    )


async def delete_job(db: AsyncIOMotorDatabase, job_id: str) -> int:
    result = await db[SOCIAL_COLLECTION_JOBS_COLLECTION].delete_one(
        {"job_id": str(job_id or "")}
    )
    return result.deleted_count


async def list_jobs(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str = "",
    status: str = "",
    platform: str = "",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    if status:
        query["status"] = status
    if platform:
        query["platforms"] = platform
    collection = db[SOCIAL_COLLECTION_JOBS_COLLECTION]
    total = await collection.count_documents(query)
    cursor = (
        collection.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(max(0, skip))
        .limit(max(1, min(limit, 200)))
    )
    return [item async for item in cursor], total


async def set_job_running(
    db: AsyncIOMotorDatabase,
    *,
    job_id: str,
    parent_task_id: str,
) -> dict[str, Any] | None:
    now = _now()
    return await db[SOCIAL_COLLECTION_JOBS_COLLECTION].find_one_and_update(
        {"job_id": job_id, "status": {"$in": list(ACTIVE_JOB_STATUSES)}},
        {
            "$set": {
                "status": "running",
                "parent_task_id": parent_task_id,
                "started_at": now,
                "updated_at": now,
            },
            "$unset": {"paused_at": ""},
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def update_platform_status(
    db: AsyncIOMotorDatabase,
    *,
    job_id: str,
    platform: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    job = await get_job(db, job_id)
    if not job:
        return
    tasks = [dict(item) for item in job.get("platform_tasks") or []]
    now = _now()
    for item in tasks:
        if str(item.get("platform") or "") != platform:
            continue
        item["status"] = status
        item["updated_at"] = now
        if status == "running" and not item.get("started_at"):
            item["started_at"] = now
        if status in {"completed", "partial", "error", "cancelled"}:
            item["completed_at"] = now
        if result is not None:
            item["result"] = result
        if error:
            item["error"] = error[:1000]
        break
    completed = sum(
        item.get("status") in {"completed", "partial"} for item in tasks
    )
    failed = sum(item.get("status") == "error" for item in tasks)
    await db[SOCIAL_COLLECTION_JOBS_COLLECTION].update_one(
        {"job_id": job_id},
        {
            "$set": {
                "platform_tasks": tasks,
                "progress.total": len(tasks),
                "progress.completed": completed,
                "progress.failed": failed,
                "updated_at": now,
            }
        },
    )


async def finish_job(
    db: AsyncIOMotorDatabase,
    *,
    job_id: str,
    status: str,
    result: dict[str, Any],
) -> None:
    await db[SOCIAL_COLLECTION_JOBS_COLLECTION].update_one(
        {"job_id": job_id},
        {
            "$set": {
                "status": status,
                "result": result,
                "progress.media_count": int(result.get("media_count") or 0),
                "completed_at": _now(),
                "updated_at": _now(),
            },
            "$unset": {"active_request_key": ""},
        },
    )


async def mark_job_paused(
    db: AsyncIOMotorDatabase,
    *,
    job_id: str,
    media_count: int,
) -> None:
    job = await get_job(db, job_id)
    if not job:
        return
    now = _now()
    tasks = [dict(item) for item in job.get("platform_tasks") or []]
    for item in tasks:
        if item.get("status") == "running":
            item["status"] = "pending"
            item["last_interrupted_at"] = now
            item.pop("completed_at", None)
    await db[SOCIAL_COLLECTION_JOBS_COLLECTION].update_one(
        {"job_id": job_id},
        {
            "$set": {
                "status": "paused",
                "platform_tasks": tasks,
                "progress.media_count": max(0, int(media_count or 0)),
                "paused_at": now,
                "updated_at": now,
            },
            "$unset": {"completed_at": ""},
        },
    )


def stable_evidence_id(
    *,
    project_id: str,
    platform: str,
    place_name: str,
    content_sha256: str,
) -> str:
    identity = "\x1f".join(
        [project_id.strip(), platform.strip(), place_name.strip().casefold(), content_sha256]
    )
    return "sme_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:24]


async def get_media_evidence(
    db: AsyncIOMotorDatabase,
    evidence_id: str,
) -> dict[str, Any] | None:
    return await db[SOCIAL_MEDIA_EVIDENCE_COLLECTION].find_one(
        {"evidence_id": evidence_id},
        {"_id": 0},
    )


async def upsert_media_evidence(
    db: AsyncIOMotorDatabase,
    *,
    evidence_id: str,
    document: dict[str, Any],
    job_id: str,
    run_task_id: str,
) -> dict[str, Any]:
    now = _now()
    collection = db[SOCIAL_MEDIA_EVIDENCE_COLLECTION]
    add_to_set: dict[str, Any] = {
        "job_ids": job_id,
        "run_task_ids": run_task_id,
    }
    for source_field, array_field in (
        ("record_id", "record_ids"),
        ("target_id", "target_ids"),
        ("task_def_id", "task_def_ids"),
        ("keyword", "keywords"),
    ):
        value = str(document.get(source_field) or "")
        if value:
            add_to_set[array_field] = value
    context_screenshot_id = str(document.get("context_screenshot_id") or "")
    context_storage_id = str(document.get("context_storage_object_id") or "")
    if context_screenshot_id:
        add_to_set["context_screenshot_ids"] = context_screenshot_id
    if context_storage_id:
        add_to_set["context_storage_object_ids"] = context_storage_id
    add_to_set["sightings"] = {
        "job_id": job_id,
        "run_task_id": run_task_id,
        "record_id": str(document.get("record_id") or ""),
        "target_id": str(document.get("target_id") or ""),
        "task_def_id": str(document.get("task_def_id") or ""),
        "keyword": str(document.get("keyword") or ""),
        "context_screenshot_id": context_screenshot_id,
        "context_storage_object_id": context_storage_id,
        "candidate_fields": dict(document.get("candidate_fields") or {}),
        "captured_at": now,
    }
    await collection.update_one(
        {"evidence_id": evidence_id},
        {
            "$set": {**document, "evidence_id": evidence_id, "last_seen": now},
            "$setOnInsert": {"created_at": now},
            "$addToSet": add_to_set,
        },
        upsert=True,
    )
    return await get_media_evidence(db, evidence_id) or {
        **document,
        "evidence_id": evidence_id,
    }


async def list_media_evidence(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str = "",
    job_id: str = "",
    platform: str = "",
    target_id: str = "",
    record_id: str = "",
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    if job_id:
        query["job_ids"] = job_id
    if platform:
        query["platform"] = platform
    if target_id:
        query["$or"] = [{"target_ids": target_id}, {"target_id": target_id}]
    if record_id:
        record_query = [{"record_ids": record_id}, {"record_id": record_id}]
        if "$or" in query:
            query["$and"] = [{"$or": query.pop("$or")}, {"$or": record_query}]
        else:
            query["$or"] = record_query
    collection = db[SOCIAL_MEDIA_EVIDENCE_COLLECTION]
    total = await collection.count_documents(query)
    cursor = (
        collection.find(query, {"_id": 0})
        .sort("created_at", -1)
        .skip(max(0, skip))
        .limit(max(1, min(limit, 200)))
    )
    return [item async for item in cursor], total


async def count_job_media(db: AsyncIOMotorDatabase, job_id: str) -> int:
    if not job_id:
        return 0
    return await db[SOCIAL_MEDIA_EVIDENCE_COLLECTION].count_documents(
        {"job_ids": job_id}
    )


async def delete_project_data(
    db: AsyncIOMotorDatabase,
    project_id: str,
) -> dict[str, Any]:
    """Delete social evidence objects before removing project metadata."""
    media = db[SOCIAL_MEDIA_EVIDENCE_COLLECTION]
    cursor = media.find(
        {"project_id": project_id},
        {"storage_object_id": 1},
    )
    object_ids: list[str] = []
    seen_object_ids: set[str] = set()
    async for doc in cursor:
        object_id = str(doc.get("storage_object_id") or "")
        if object_id and object_id not in seen_object_ids:
            seen_object_ids.add(object_id)
            object_ids.append(object_id)
    deleted_objects = 0
    storage_errors: list[str] = []
    if object_ids:
        try:
            from api.storage import get_object_storage

            storage = await get_object_storage()
            semaphore = asyncio.Semaphore(16)

            async def _delete(object_id: str) -> tuple[str, str]:
                async with semaphore:
                    try:
                        await storage.delete(object_id)
                        return object_id, ""
                    except FileNotFoundError:
                        return object_id, ""
                    except Exception as exc:  # noqa: BLE001
                        return object_id, str(exc)

            for object_id, error in await asyncio.gather(
                *(_delete(object_id) for object_id in object_ids)
            ):
                if error:
                    storage_errors.append(f"{object_id}: {error}")
                else:
                    deleted_objects += 1
        except Exception as exc:  # noqa: BLE001
            storage_errors.append(f"storage provider: {exc}")

    media_result, jobs_result = await asyncio.gather(
        media.delete_many({"project_id": project_id}),
        db[SOCIAL_COLLECTION_JOBS_COLLECTION].delete_many(
            {"project_id": project_id}
        ),
    )
    return {
        "media_deleted": media_result.deleted_count,
        "jobs_deleted": jobs_result.deleted_count,
        "storage_objects_deleted": deleted_objects,
        "storage_errors": storage_errors,
    }
