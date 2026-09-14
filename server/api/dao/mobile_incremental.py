"""Durable time windows in mobile checkpoints; cursors belong to ProjectTarget."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ReturnDocument

from api.db.collections import (
    MOBILE_COLLECT_CHECKPOINTS_COLLECTION,
    PROJECT_TARGETS_COLLECTION,
)


async def get_target_state(db: Any, project_id: str, target_id: str) -> dict:
    return await db[PROJECT_TARGETS_COLLECTION].find_one(
        {"project_id": project_id, "target_id": target_id, "active": {"$ne": False}},
        {"_id": 0, "mobile_incremental_cursors": 1, "mobile_incremental_baseline": 1},
    ) or {}


async def get_window(db: Any, run_task_id: str, checkpoint_key: str) -> dict | None:
    return await db[MOBILE_COLLECT_CHECKPOINTS_COLLECTION].find_one(
        {"run_task_id": run_task_id, "checkpoint_key": checkpoint_key}, {"_id": 0},
    )


async def save_window(db: Any, window: dict) -> dict:
    return await db[MOBILE_COLLECT_CHECKPOINTS_COLLECTION].find_one_and_update(
        {key: window[key] for key in ("run_task_id", "checkpoint_key")},
        {"$setOnInsert": window}, upsert=True, return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )


async def advance_cursor(
    db: Any, *, project_id: str, target_id: str, scope_key: str,
    through_at: datetime, run_task_id: str,
) -> None:
    path = f"mobile_incremental_cursors.{scope_key}"
    await db[PROJECT_TARGETS_COLLECTION].update_one(
        {
            "project_id": project_id, "target_id": target_id,
            "$or": [{f"{path}.through_at": {"$exists": False}},
                    {f"{path}.through_at": {"$lt": through_at}}],
        },
        {"$set": {path: {"through_at": through_at, "run_task_id": run_task_id}}},
    )


async def set_baseline(
    db: Any, *, project_id: str, target_id: str, since: datetime,
    source_project_id: str, source_task_id: str, reason: str,
) -> None:
    """Explicit historical import; never infer a mobile cursor from website activity."""
    result = await db[PROJECT_TARGETS_COLLECTION].update_one(
        {"project_id": project_id, "target_id": target_id},
        {"$set": {"mobile_incremental_baseline": {
            "since": since, "source_project_id": source_project_id,
            "source_task_id": source_task_id, "reason": reason,
        }}},
    )
    if not result.matched_count:
        raise ValueError("增量起点对应的项目 Target 不存在")


async def inherit_target_state(db: Any, source: dict, destination_project_id: str) -> None:
    """Retain imported scope cursors without moving a destination backwards."""
    query = {"project_id": destination_project_id, "target_id": source["target_id"]}
    baseline = source.get("mobile_incremental_baseline")
    if baseline:
        await db[PROJECT_TARGETS_COLLECTION].update_one(
            {**query, "mobile_incremental_baseline": {"$exists": False}},
            {"$set": {"mobile_incremental_baseline": baseline}},
        )
    for scope, cursor in (source.get("mobile_incremental_cursors") or {}).items():
        if len(scope) != 24 or any(char not in "0123456789abcdef" for char in scope):
            continue
        if cursor.get("through_at"):
            await advance_cursor(db, **query, scope_key=scope, through_at=cursor["through_at"],
                                 run_task_id=str(cursor.get("run_task_id") or ""))
