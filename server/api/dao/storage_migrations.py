"""对象存储迁移运行记录 DAO。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import STORAGE_MIGRATIONS_COLLECTION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[STORAGE_MIGRATIONS_COLLECTION]
    await coll.create_index("run_id", unique=True)
    await coll.create_index("started_at")
    await coll.create_index("status")


async def start(db: AsyncIOMotorDatabase, run_id: str, config: dict[str, Any]) -> None:
    await db[STORAGE_MIGRATIONS_COLLECTION].update_one(
        {"run_id": run_id},
        {
            "$setOnInsert": {
                "run_id": run_id,
                "status": "running",
                "started_at": _now(),
                "config": config,
                "counters": {},
                "failures": [],
            }
        },
        upsert=True,
    )


async def progress(
    db: AsyncIOMotorDatabase,
    run_id: str,
    *,
    counter: str,
    amount: int = 1,
    bytes_count: int = 0,
) -> None:
    inc: dict[str, int] = {f"counters.{counter}": amount}
    if bytes_count:
        inc["counters.bytes"] = bytes_count
    await db[STORAGE_MIGRATIONS_COLLECTION].update_one(
        {"run_id": run_id},
        {"$inc": inc, "$set": {"updated_at": _now()}},
    )


async def add_failure(db: AsyncIOMotorDatabase, run_id: str, failure: dict[str, Any]) -> None:
    await db[STORAGE_MIGRATIONS_COLLECTION].update_one(
        {"run_id": run_id},
        {
            "$inc": {"counters.failed": 1},
            "$push": {"failures": {"$each": [failure], "$slice": -1000}},
            "$set": {"updated_at": _now()},
        },
    )


async def finish(db: AsyncIOMotorDatabase, run_id: str, *, status: str) -> dict[str, Any]:
    await db[STORAGE_MIGRATIONS_COLLECTION].update_one(
        {"run_id": run_id},
        {"$set": {"status": status, "finished_at": _now(), "updated_at": _now()}},
    )
    return await db[STORAGE_MIGRATIONS_COLLECTION].find_one({"run_id": run_id}, {"_id": 0}) or {}


async def latest(db: AsyncIOMotorDatabase) -> dict[str, Any] | None:
    return await db[STORAGE_MIGRATIONS_COLLECTION].find_one(
        {},
        {
            "_id": 0,
            "run_id": 1,
            "status": 1,
            "started_at": 1,
            "finished_at": 1,
            "updated_at": 1,
            "counters": 1,
        },
        sort=[("started_at", -1)],
    )
