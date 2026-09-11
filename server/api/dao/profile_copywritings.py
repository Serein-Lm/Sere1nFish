"""Idempotent persistence for XHS profile copywriting outputs."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import PROFILE_COPYWRITINGS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def stable_copywriting_id(
    *,
    project_id: str,
    task_id: str,
    target_id: str,
    user_id: str,
    variant_index: int,
) -> str:
    identity = "\x1f".join(
        (
            str(project_id or "").strip(),
            str(task_id or "").strip(),
            str(target_id or "").strip(),
            str(user_id or "").strip(),
            str(max(0, int(variant_index))),
        )
    )
    return "profile_cw_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


async def upsert_generated(
    db: AsyncIOMotorDatabase,
    document: dict[str, Any],
    *,
    variant_index: int,
) -> dict[str, Any]:
    """Persist one generated variant without duplicating recovery retries."""
    payload = dict(document)
    copywriting_id = stable_copywriting_id(
        project_id=str(payload.get("project_id") or ""),
        task_id=str(payload.get("task_id") or ""),
        target_id=str(payload.get("target_id") or ""),
        user_id=str(payload.get("user_id") or ""),
        variant_index=variant_index,
    )
    now = _now()
    created_at = payload.pop("created_at", now)
    payload.pop("_id", None)
    payload.update(
        copywriting_id=copywriting_id,
        variant_index=max(0, int(variant_index)),
        updated_at=now,
    )
    await db[PROFILE_COPYWRITINGS_COLLECTION].update_one(
        {"copywriting_id": copywriting_id},
        {
            "$set": payload,
            "$setOnInsert": {"created_at": created_at},
        },
        upsert=True,
    )
    return payload


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[PROFILE_COPYWRITINGS_COLLECTION]
    await collection.create_index("copywriting_id", unique=True, sparse=True)
    await collection.create_index("task_id")
    await collection.create_index(
        [("project_id", 1), ("target_id", 1), ("updated_at", -1)],
        sparse=True,
    )
