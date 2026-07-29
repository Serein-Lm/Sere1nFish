"""Durable progress records for persona generation and enrichment tasks."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import PERSONA_RESEARCH_TASKS_COLLECTION


ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[PERSONA_RESEARCH_TASKS_COLLECTION]
    await coll.create_index("task_id", unique=True)
    await coll.create_index([("status", 1), ("updated_at", -1)])
    await coll.create_index([("person_id", 1), ("created_at", -1)])
    await coll.create_index([("project_id", 1), ("created_at", -1)])


async def mark_interrupted(db: AsyncIOMotorDatabase) -> int:
    """Close tasks that belonged to a previous application process."""
    now = _now()
    result = await db[PERSONA_RESEARCH_TASKS_COLLECTION].update_many(
        {"status": {"$in": list(ACTIVE_STATUSES)}},
        {
            "$set": {
                "status": "cancelled",
                "stage": "interrupted",
                "message": "服务进程重启，任务已中断，请重新发起",
                "error": "runtime_restarted",
                "finished_at": now,
                "updated_at": now,
            }
        },
    )
    return int(result.modified_count)


async def create_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    task_type: str,
    requested_count: int,
    person_id: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    now = _now()
    doc = {
        "task_id": task_id,
        "task_type": task_type,
        "status": "queued",
        "stage": "queued",
        "message": "任务已进入队列",
        "requested_count": max(1, int(requested_count)),
        "completed_count": 0,
        "failed_count": 0,
        "person_id": str(person_id or ""),
        "project_id": str(project_id or ""),
        "result_person_ids": [],
        "error": "",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }
    await db[PERSONA_RESEARCH_TASKS_COLLECTION].insert_one(doc)
    return {key: value for key, value in doc.items() if key != "_id"}


async def update_task(
    db: AsyncIOMotorDatabase,
    task_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    message: str | None = None,
    completed_count: int | None = None,
    failed_count: int | None = None,
    result_person_ids: list[str] | None = None,
    error: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    now = _now()
    fields: dict[str, Any] = {"updated_at": now}
    if status is not None:
        normalized_status = str(status).strip().lower()
        if normalized_status not in {*ACTIVE_STATUSES, *TERMINAL_STATUSES}:
            raise ValueError(f"不支持的人设研究任务状态: {status}")
        fields["status"] = normalized_status
        if normalized_status == "running":
            current = await db[PERSONA_RESEARCH_TASKS_COLLECTION].find_one(
                {"task_id": task_id},
                {"_id": 0, "started_at": 1},
            )
            if not current or not current.get("started_at"):
                fields["started_at"] = now
        if normalized_status in TERMINAL_STATUSES:
            fields["finished_at"] = now
    if stage is not None:
        fields["stage"] = str(stage or "unknown")[:80]
    if message is not None:
        fields["message"] = str(message or "")[:500]
    if completed_count is not None:
        fields["completed_count"] = max(0, int(completed_count))
    if failed_count is not None:
        fields["failed_count"] = max(0, int(failed_count))
    if result_person_ids is not None:
        fields["result_person_ids"] = list(
            dict.fromkeys(str(value).strip() for value in result_person_ids if str(value).strip())
        )
    if error is not None:
        fields["error"] = str(error or "")[:1000]
    if details is not None:
        fields["details"] = dict(details)
    await db[PERSONA_RESEARCH_TASKS_COLLECTION].update_one(
        {"task_id": task_id},
        {"$set": fields},
    )
    return await get_task(db, task_id)


async def get_task(
    db: AsyncIOMotorDatabase,
    task_id: str,
) -> dict[str, Any] | None:
    return await db[PERSONA_RESEARCH_TASKS_COLLECTION].find_one(
        {"task_id": task_id},
        {"_id": 0},
    )


async def get_active_person_task(
    db: AsyncIOMotorDatabase,
    person_id: str,
) -> dict[str, Any] | None:
    return await db[PERSONA_RESEARCH_TASKS_COLLECTION].find_one(
        {
            "person_id": str(person_id or ""),
            "status": {"$in": list(ACTIVE_STATUSES)},
        },
        {"_id": 0},
        sort=[("created_at", -1)],
    )
