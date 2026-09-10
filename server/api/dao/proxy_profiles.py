"""Encrypted SOCKS5 profiles and bounded per-work proxy leases."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db.collections import PROXY_LEASES_COLLECTION, PROXY_PROFILES_COLLECTION
from api.utils.config_crypto import decrypt_value, encrypt_value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _endpoint_hint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.hostname and parsed.port:
            return f"{parsed.hostname}:{parsed.port}"
    except ValueError:
        pass
    return "已配置" if value else ""


def _public_profile(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    endpoint = str(decrypt_value(doc.get("endpoint")) or "")
    username = str(decrypt_value(doc.get("username")) or "")
    password = str(decrypt_value(doc.get("password")) or "")
    return {
        key: value
        for key, value in doc.items()
        if key not in {"_id", "endpoint", "username", "password"}
    } | {
        "endpoint_hint": _endpoint_hint(endpoint),
        "has_username": bool(username),
        "has_password": bool(password),
    }


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    profiles = db[PROXY_PROFILES_COLLECTION]
    await profiles.create_index("profile_id", unique=True)
    await profiles.create_index("name", unique=True)
    await profiles.create_index([("status", ASCENDING), ("cooldown_until", ASCENDING)])

    leases = db[PROXY_LEASES_COLLECTION]
    await leases.create_index("lease_id", unique=True)
    await leases.create_index("work_item_id", unique=True)
    await leases.create_index([("profile_id", ASCENDING), ("status", ASCENDING)])
    await leases.create_index([("node_id", ASCENDING), ("status", ASCENDING)])
    await leases.create_index("expires_at")


async def upsert_profile(
    db: AsyncIOMotorDatabase,
    *,
    profile_id: str,
    name: str,
    endpoint: str | None,
    username: str | None,
    password: str | None,
    dns_mode: str,
    max_concurrency: int,
    failure_threshold: int,
    cooldown_seconds: int,
    bypass_classes: list[str],
    labels: dict[str, str],
    status: str,
    updated_by: str,
) -> dict[str, Any]:
    now = _now()
    existing = await db[PROXY_PROFILES_COLLECTION].find_one(
        {"profile_id": profile_id},
        {"_id": 0},
    )
    fields: dict[str, Any] = {
        "profile_id": profile_id,
        "name": name,
        "type": "socks5",
        "dns_mode": dns_mode,
        "max_concurrency": max_concurrency,
        "failure_threshold": failure_threshold,
        "cooldown_seconds": cooldown_seconds,
        "bypass_classes": bypass_classes,
        "labels": labels,
        "status": status,
        "updated_by": updated_by,
        "updated_at": now,
    }
    if endpoint is not None:
        fields["endpoint"] = encrypt_value(endpoint)
    elif not existing:
        raise ValueError("SOCKS5 endpoint 不能为空")
    if username is not None:
        fields["username"] = encrypt_value(username)
    if password is not None:
        fields["password"] = encrypt_value(password)
    doc = await db[PROXY_PROFILES_COLLECTION].find_one_and_update(
        {"profile_id": profile_id},
        {
            "$set": fields,
            "$setOnInsert": {
                "created_at": now,
                "active_leases": 0,
                "consecutive_failures": 0,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _public_profile(doc) or {}


async def get_profile(
    db: AsyncIOMotorDatabase,
    profile_id: str,
    *,
    reveal: bool = False,
) -> dict[str, Any] | None:
    doc = await db[PROXY_PROFILES_COLLECTION].find_one(
        {"profile_id": profile_id},
        {"_id": 0},
    )
    if not doc:
        return None
    if not reveal:
        return _public_profile(doc)
    result = dict(doc)
    for key in ("endpoint", "username", "password"):
        result[key] = str(decrypt_value(result.get(key)) or "")
    return result


async def list_profiles(
    db: AsyncIOMotorDatabase,
    *,
    status: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    cursor = (
        db[PROXY_PROFILES_COLLECTION]
        .find(query, {"_id": 0})
        .sort([("status", ASCENDING), ("name", ASCENDING)])
        .limit(max(1, min(limit, 2000)))
    )
    return [_public_profile(doc) or {} async for doc in cursor]


async def delete_profile(db: AsyncIOMotorDatabase, profile_id: str) -> bool:
    result = await db[PROXY_PROFILES_COLLECTION].delete_one(
        {"profile_id": profile_id, "active_leases": {"$lte": 0}}
    )
    return bool(result.deleted_count)


async def acquire_lease(
    db: AsyncIOMotorDatabase,
    *,
    lease_id: str,
    profile_id: str,
    node_id: str,
    work_item_id: str,
    work_attempt: int,
    sticky_key: str,
    expires_at: datetime,
) -> dict[str, Any] | None:
    now = _now()
    leases = db[PROXY_LEASES_COLLECTION]
    existing = await leases.find_one(
        {"work_item_id": work_item_id, "status": "active"},
        {"_id": 0},
    )
    if existing:
        same_attempt = (
            existing.get("node_id") == node_id
            and int(existing.get("work_attempt") or 0) == work_attempt
            and existing.get("profile_id") == profile_id
        )
        if same_attempt:
            await leases.update_one(
                {"lease_id": existing["lease_id"], "status": "active"},
                {"$set": {"expires_at": expires_at, "updated_at": now}},
            )
            profile = await db[PROXY_PROFILES_COLLECTION].find_one(
                {"profile_id": profile_id},
                {"_id": 0},
            )
            if not profile:
                return None
            revealed = dict(profile)
            for key in ("endpoint", "username", "password"):
                revealed[key] = str(decrypt_value(revealed.get(key)) or "")
            return {"lease": existing, "profile": revealed}

        # The work lease is authoritative. A proxy lease from an older work
        # attempt must not block the retry, even if the sweeper has not run yet.
        await release_work_lease(
            db,
            work_item_id=work_item_id,
            status="superseded",
        )

    profile = await db[PROXY_PROFILES_COLLECTION].find_one_and_update(
        {
            "profile_id": profile_id,
            "status": "active",
            "$or": [
                {"cooldown_until": {"$exists": False}},
                {"cooldown_until": {"$lte": now}},
            ],
            "$expr": {
                "$lt": [
                    {"$ifNull": ["$active_leases", 0]},
                    {"$ifNull": ["$max_concurrency", 1]},
                ]
            },
        },
        {"$inc": {"active_leases": 1}, "$set": {"updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not profile:
        return None
    lease = {
        "lease_id": lease_id,
        "profile_id": profile_id,
        "node_id": node_id,
        "work_item_id": work_item_id,
        "work_attempt": work_attempt,
        "sticky_key": sticky_key,
        "status": "active",
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
    }
    try:
        stored_lease = await leases.find_one_and_update(
            {
                "work_item_id": work_item_id,
                "status": {"$ne": "active"},
            },
            {
                "$set": lease,
                "$unset": {"released_at": ""},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        await db[PROXY_PROFILES_COLLECTION].update_one(
            {"profile_id": profile_id, "active_leases": {"$gt": 0}},
            {"$inc": {"active_leases": -1}},
        )
        concurrent = await leases.find_one(
            {
                "work_item_id": work_item_id,
                "node_id": node_id,
                "work_attempt": work_attempt,
                "profile_id": profile_id,
                "status": "active",
            },
            {"_id": 0},
        )
        if not concurrent:
            return None
        stored_lease = concurrent
        profile = await db[PROXY_PROFILES_COLLECTION].find_one(
            {"profile_id": profile_id},
            {"_id": 0},
        )
        if not profile:
            return None
    except Exception:
        await db[PROXY_PROFILES_COLLECTION].update_one(
            {"profile_id": profile_id, "active_leases": {"$gt": 0}},
            {"$inc": {"active_leases": -1}},
        )
        raise
    revealed = dict(profile)
    for key in ("endpoint", "username", "password"):
        revealed[key] = str(decrypt_value(revealed.get(key)) or "")
    return {
        "lease": {
            key: value for key, value in (stored_lease or lease).items() if key != "_id"
        },
        "profile": revealed,
    }


async def get_work_lease(
    db: AsyncIOMotorDatabase,
    work_item_id: str,
) -> dict[str, Any] | None:
    return await db[PROXY_LEASES_COLLECTION].find_one(
        {"work_item_id": work_item_id, "status": "active"},
        {"_id": 0},
    )


async def release_work_lease(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    status: str = "released",
) -> bool:
    now = _now()
    lease = await db[PROXY_LEASES_COLLECTION].find_one_and_update(
        {"work_item_id": work_item_id, "status": "active"},
        {"$set": {"status": status, "released_at": now, "updated_at": now}},
        return_document=ReturnDocument.BEFORE,
    )
    if not lease:
        return False
    await db[PROXY_PROFILES_COLLECTION].update_one(
        {"profile_id": lease["profile_id"], "active_leases": {"$gt": 0}},
        {"$inc": {"active_leases": -1}, "$set": {"updated_at": now}},
    )
    return True


async def renew_work_lease(
    db: AsyncIOMotorDatabase,
    *,
    work_item_id: str,
    expires_at: datetime,
) -> bool:
    result = await db[PROXY_LEASES_COLLECTION].update_one(
        {"work_item_id": work_item_id, "status": "active"},
        {"$set": {"expires_at": expires_at, "updated_at": _now()}},
    )
    return bool(result.matched_count)


async def report_result(
    db: AsyncIOMotorDatabase,
    *,
    profile_id: str,
    ok: bool,
    error_code: str = "",
) -> None:
    now = _now()
    if ok:
        await db[PROXY_PROFILES_COLLECTION].update_one(
            {"profile_id": profile_id},
            {
                "$set": {"consecutive_failures": 0, "last_success_at": now, "updated_at": now},
                "$unset": {"cooldown_until": "", "last_error_code": ""},
            },
        )
        return
    profile = await db[PROXY_PROFILES_COLLECTION].find_one_and_update(
        {"profile_id": profile_id},
        {
            "$inc": {"consecutive_failures": 1},
            "$set": {"last_failure_at": now, "last_error_code": error_code[:100], "updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not profile:
        return
    threshold = max(1, int(profile.get("failure_threshold") or 3))
    if int(profile.get("consecutive_failures") or 0) >= threshold:
        cooldown = max(10, min(int(profile.get("cooldown_seconds") or 300), 86400))
        await db[PROXY_PROFILES_COLLECTION].update_one(
            {"profile_id": profile_id},
            {"$set": {"cooldown_until": now + timedelta(seconds=cooldown), "updated_at": now}},
        )


async def expire_leases(db: AsyncIOMotorDatabase, *, limit: int = 1000) -> int:
    now = _now()
    cursor = (
        db[PROXY_LEASES_COLLECTION]
        .find(
            {"status": "active", "expires_at": {"$lte": now}},
            {"_id": 0, "work_item_id": 1},
        )
        .limit(max(1, min(limit, 5000)))
    )
    expired = 0
    async for lease in cursor:
        expired += int(
            await release_work_lease(
                db,
                work_item_id=str(lease["work_item_id"]),
                status="expired",
            )
        )
    return expired


async def get_stats(db: AsyncIOMotorDatabase) -> dict[str, int]:
    rows = await db[PROXY_PROFILES_COLLECTION].aggregate(
        [{"$group": {"_id": "$status", "count": {"$sum": 1}, "active": {"$sum": {"$ifNull": ["$active_leases", 0]}}}}]
    ).to_list(length=None)
    result = {"total": 0, "active": 0, "disabled": 0, "active_leases": 0}
    for row in rows:
        status = str(row.get("_id") or "disabled")
        count = int(row.get("count") or 0)
        result["total"] += count
        result[status] = count
        result["active_leases"] += int(row.get("active") or 0)
    return result
