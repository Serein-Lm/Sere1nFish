"""Atomic projection of externally blocked tasks onto resumable paused state."""
from datetime import datetime, timezone
from typing import Any

from api.db.collections import TASKS_COLLECTION


async def pause_for_dependency(db, *, task_id: str, runtime_id: str,
                               dependency: dict[str, Any], result: dict[str, Any]) -> bool:
    now = datetime.now(timezone.utc)
    updated = await db[TASKS_COLLECTION].update_one(
        {"task_id": task_id, "runtime_id": runtime_id,
         "status": {"$in": ["running", "waiting_mobile", "waiting_core", "waiting_model"]}},
        {"$set": {"status": "paused", "progress.stage": "paused",
                  "progress.message": dependency["message"],
                  "progress.last_activity_at": now, "blocked_dependency": dependency,
                  "result": result, "result_status": "partial",
                  "paused_at": now, "updated_at": now},
         "$unset": {"runtime_id": "", "heartbeat_at": ""}},
    )
    return bool(updated.modified_count)
