"""Mongo-backed exclusive execution leases for mobile devices."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db.collections import MOBILE_EXECUTION_LEASES_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[MOBILE_EXECUTION_LEASES_COLLECTION]
    await coll.create_index("device_key", unique=True)
    await coll.create_index("lease_id", unique=True)
    await coll.create_index("task_id")
    await coll.create_index("requested_by")
    await coll.create_index("expires_at", expireAfterSeconds=0)


async def try_acquire(
    db: AsyncIOMotorDatabase,
    *,
    device_key: str,
    lease_id: str,
    runtime_id: str,
    task_id: str,
    owner: str,
    requested_by: str,
    kind: str,
    device_id: str,
    ttl_seconds: float,
) -> dict[str, Any] | None:
    """Atomically acquire an absent/expired lease.

    An active document that does not match ``lease_id`` makes the upsert hit the
    unique ``device_key`` index.  The duplicate-key result is the expected busy
    path and prevents two server processes from controlling one phone.
    """
    now = _now()
    expires_at = now + timedelta(seconds=max(10.0, float(ttl_seconds)))
    query = {
        "device_key": device_key,
        "$or": [
            {"lease_id": lease_id},
            {"expires_at": {"$lte": now}},
            {"expires_at": {"$exists": False}},
        ],
    }
    try:
        return await db[MOBILE_EXECUTION_LEASES_COLLECTION].find_one_and_update(
            query,
            {
                "$set": {
                    "device_key": device_key,
                    "device_id": device_id,
                    "lease_id": lease_id,
                    "runtime_id": runtime_id,
                    "task_id": task_id,
                    "owner": owner,
                    "requested_by": requested_by,
                    "kind": kind,
                    "expires_at": expires_at,
                    "heartbeat_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
                "$unset": {"cancel_requested_at": "", "cancel_requested_by": ""},
            },
            upsert=True,
            projection={"_id": 0},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return None


async def renew(
    db: AsyncIOMotorDatabase,
    *,
    device_key: str,
    lease_id: str,
    runtime_id: str,
    ttl_seconds: float,
) -> bool:
    now = _now()
    result = await db[MOBILE_EXECUTION_LEASES_COLLECTION].update_one(
        {
            "device_key": device_key,
            "lease_id": lease_id,
            "runtime_id": runtime_id,
            "expires_at": {"$gt": now},
        },
        {
            "$set": {
                "heartbeat_at": now,
                "updated_at": now,
                "expires_at": now
                + timedelta(seconds=max(10.0, float(ttl_seconds))),
            }
        },
    )
    return bool(result.modified_count)


async def release(
    db: AsyncIOMotorDatabase,
    *,
    device_key: str,
    lease_id: str,
    runtime_id: str,
) -> bool:
    result = await db[MOBILE_EXECUTION_LEASES_COLLECTION].delete_one(
        {
            "device_key": device_key,
            "lease_id": lease_id,
            "runtime_id": runtime_id,
        }
    )
    return bool(result.deleted_count)


async def get_active_by_device(
    db: AsyncIOMotorDatabase,
    device_key: str,
) -> dict[str, Any] | None:
    return await db[MOBILE_EXECUTION_LEASES_COLLECTION].find_one(
        {"device_key": device_key, "expires_at": {"$gt": _now()}},
        {"_id": 0},
    )


async def get_by_lease(
    db: AsyncIOMotorDatabase,
    *,
    lease_id: str,
    runtime_id: str,
) -> dict[str, Any] | None:
    return await db[MOBILE_EXECUTION_LEASES_COLLECTION].find_one(
        {
            "lease_id": lease_id,
            "runtime_id": runtime_id,
            "expires_at": {"$gt": _now()},
        },
        {"_id": 0},
    )


async def request_cancel(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    requested_by: str,
    is_admin: bool = False,
) -> bool:
    query: dict[str, Any] = {
        "task_id": task_id,
        "expires_at": {"$gt": _now()},
    }
    if not is_admin:
        query["requested_by"] = requested_by
    now = _now()
    result = await db[MOBILE_EXECUTION_LEASES_COLLECTION].update_one(
        query,
        {
            "$set": {
                "cancel_requested_at": now,
                "cancel_requested_by": requested_by,
                "updated_at": now,
            }
        },
    )
    return bool(result.matched_count)


async def list_active(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db[MOBILE_EXECUTION_LEASES_COLLECTION].find(
        {"expires_at": {"$gt": _now()}},
        {"_id": 0},
    )
    return [item async for item in cursor]


async def cleanup_expired(db: AsyncIOMotorDatabase) -> int:
    result = await db[MOBILE_EXECUTION_LEASES_COLLECTION].delete_many(
        {"expires_at": {"$lte": _now()}}
    )
    return int(result.deleted_count)
