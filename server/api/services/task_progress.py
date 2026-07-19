"""Unified durable progress updates for long-running project tasks."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import TASKS_COLLECTION


_SOURCE_KEY_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _source_key(value: str) -> str:
    key = _SOURCE_KEY_RE.sub("_", str(value or "source").strip()).strip("_")
    return key[:48] or "source"


async def update_source_progress(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    source: str,
    status: str = "running",
    processed: int | None = None,
    total: int | None = None,
    succeeded: int | None = None,
    failed: int | None = None,
    skipped: int | None = None,
    message: str = "",
    extra: dict[str, Any] | None = None,
) -> bool:
    """Record real business activity without replacing the parent stage."""
    if not task_id:
        return False
    now = datetime.now(timezone.utc)
    key = _source_key(source)
    prefix = f"progress.sources.{key}"
    fields: dict[str, Any] = {
        f"{prefix}.source": source,
        f"{prefix}.status": status,
        f"{prefix}.updated_at": now,
        "progress.last_activity_at": now,
        "updated_at": now,
    }
    for name, value in (
        ("processed", processed),
        ("total", total),
        ("succeeded", succeeded),
        ("failed", failed),
        ("skipped", skipped),
    ):
        if value is not None:
            fields[f"{prefix}.{name}"] = max(0, int(value))
    if message:
        fields[f"{prefix}.message"] = str(message)[:500]
    for name, value in (extra or {}).items():
        safe_name = _source_key(name)
        fields[f"{prefix}.{safe_name}"] = value
    result = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id, "status": {"$in": ["pending", "running"]}},
        {"$set": fields},
    )
    return bool(result.matched_count)


async def save_task_checkpoint(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    phase: str,
    data: dict[str, Any] | None = None,
) -> bool:
    """Persist an idempotent phase checkpoint used by restart recovery."""
    if not task_id:
        return False
    now = datetime.now(timezone.utc)
    fields: dict[str, Any] = {
        "checkpoint.phase": str(phase or ""),
        "checkpoint.updated_at": now,
        "updated_at": now,
    }
    if data is not None:
        fields["checkpoint.data"] = data
    result = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id},
        {"$set": fields},
    )
    return bool(result.matched_count)


async def save_module_checkpoint(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    module: str,
    result: dict[str, Any],
) -> bool:
    """Store one independently completed company source for restart reuse."""
    if not task_id:
        return False
    now = datetime.now(timezone.utc)
    key = _source_key(module)
    prefix = f"checkpoint.modules.{key}"
    updated = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id},
        {
            "$set": {
                f"{prefix}.status": "completed",
                f"{prefix}.result": result,
                f"{prefix}.updated_at": now,
                "checkpoint.updated_at": now,
                "updated_at": now,
            }
        },
    )
    return bool(updated.matched_count)


async def mark_resume_phase(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    phase: str,
) -> bool:
    """Mark a durable phase boundary before releasing its runtime resources."""
    return await mark_resume_phases(db, task_id=task_id, phases=[phase])


async def mark_resume_phases(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    phases: list[str],
) -> bool:
    """Atomically mark one or more durable phase boundaries."""
    if not task_id or not phases:
        return False
    now = datetime.now(timezone.utc)
    fields = {
        f"resume.{_source_key(phase)}": True
        for phase in phases
        if str(phase or "").strip()
    }
    if not fields:
        return False
    fields.update(
        {
            "resume.updated_at": now,
            "updated_at": now,
        }
    )
    updated = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id},
        {"$set": fields},
    )
    return bool(updated.matched_count)
