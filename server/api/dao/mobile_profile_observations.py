"""Detailed mobile contact profile observations.

The contact profile document is a current snapshot. This collection stores the
append-only evidence/events used to build that snapshot so later analytics can
aggregate by project, contact, finding, device, platform, or extracted traits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import MOBILE_PROFILE_OBSERVATIONS_COLLECTION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _limit(value: int) -> int:
    return max(1, min(int(value or 100), 500))


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[MOBILE_PROFILE_OBSERVATIONS_COLLECTION]
    try:
        await coll.create_index("observation_id", unique=True)
    except Exception:
        pass
    await coll.create_index([("project_id", 1), ("created_at", -1)])
    await coll.create_index([("contact_id", 1), ("created_at", -1)])
    await coll.create_index([("finding_id", 1), ("created_at", -1)])
    await coll.create_index([("project_id", 1), ("contact_id", 1), ("created_at", -1)])
    await coll.create_index("task_id")
    await coll.create_index("device_id")
    await coll.create_index("platform")
    await coll.create_index("source")
    await coll.create_index("persona_patch.tags")
    await coll.create_index("persona_patch.risk_signals")


async def insert_observation(
    db: AsyncIOMotorDatabase,
    *,
    contact_id: str,
    project_id: str | None = None,
    finding_id: str | None = None,
    task_id: str | None = None,
    device_id: str | None = None,
    platform: str | None = None,
    contact_name: str | None = None,
    source: str = "profile_analyze",
    screen_analysis: str = "",
    persona_patch: dict[str, Any] | None = None,
    persona_snapshot: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = {
        "observation_id": "mpo_" + uuid.uuid4().hex,
        "project_id": project_id,
        "finding_id": finding_id,
        "task_id": task_id,
        "device_id": device_id,
        "contact_id": contact_id,
        "platform": platform,
        "contact_name": contact_name,
        "source": source,
        "screen_analysis": (screen_analysis or "")[:6000],
        "persona_patch": persona_patch or {},
        "persona_snapshot": persona_snapshot or {},
        "evidence": evidence or {},
        "metrics": metrics or {},
        "created_at": _now(),
    }
    await db[MOBILE_PROFILE_OBSERVATIONS_COLLECTION].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def list_observations(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str | None = None,
    contact_id: str | None = None,
    finding_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if project_id:
        query["project_id"] = project_id
    if contact_id:
        query["contact_id"] = contact_id
    if finding_id:
        query["finding_id"] = finding_id
    if task_id:
        query["task_id"] = task_id
    cursor = (
        db[MOBILE_PROFILE_OBSERVATIONS_COLLECTION]
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(_limit(limit))
    )
    return [doc async for doc in cursor]


async def delete_project_observations(
    db: AsyncIOMotorDatabase, project_id: str
) -> int:
    result = await db[MOBILE_PROFILE_OBSERVATIONS_COLLECTION].delete_many(
        {"project_id": project_id}
    )
    return result.deleted_count
