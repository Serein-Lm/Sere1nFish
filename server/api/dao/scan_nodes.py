"""Persistence for distributed scan-node identities and bootstrap tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db.collections import (
    SCAN_NODE_BOOTSTRAP_TOKENS_COLLECTION,
    SCAN_NODE_NONCES_COLLECTION,
    SCAN_NODES_COLLECTION,
)
from api.utils.config_crypto import decrypt_value, encrypt_value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_node(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return None
    return {
        key: value
        for key, value in doc.items()
        if key not in {"_id", "token_digest", "token_secret", "bootstrap_id"}
    }


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    nodes = db[SCAN_NODES_COLLECTION]
    await nodes.create_index("node_id", unique=True)
    await nodes.create_index("token_digest", unique=True)
    await nodes.create_index([("status", ASCENDING), ("last_heartbeat_at", DESCENDING)])
    await nodes.create_index("identity_fingerprint", sparse=True)

    nonces = db[SCAN_NODE_NONCES_COLLECTION]
    await nonces.create_index([("node_id", ASCENDING), ("nonce", ASCENDING)], unique=True)
    await nonces.create_index("expires_at", expireAfterSeconds=0)

    bootstraps = db[SCAN_NODE_BOOTSTRAP_TOKENS_COLLECTION]
    await bootstraps.create_index("bootstrap_id", unique=True)
    await bootstraps.create_index("token_digest", unique=True)
    await bootstraps.create_index("expires_at", expireAfterSeconds=0)
    await bootstraps.create_index([("used_at", ASCENDING), ("expires_at", ASCENDING)])


async def create_bootstrap(
    db: AsyncIOMotorDatabase,
    *,
    bootstrap_id: str,
    token_digest: str,
    display_name: str,
    allowed_capabilities: list[str],
    labels: dict[str, str],
    expires_at: datetime,
    issued_by: str,
) -> dict[str, Any]:
    now = _now()
    doc = {
        "bootstrap_id": bootstrap_id,
        "token_digest": token_digest,
        "display_name": display_name,
        "allowed_capabilities": allowed_capabilities,
        "labels": labels,
        "expires_at": expires_at,
        "issued_by": issued_by,
        "created_at": now,
    }
    await db[SCAN_NODE_BOOTSTRAP_TOKENS_COLLECTION].insert_one(doc)
    return {key: value for key, value in doc.items() if key != "token_digest"}


async def consume_bootstrap(
    db: AsyncIOMotorDatabase,
    *,
    token_digest: str,
) -> dict[str, Any] | None:
    now = _now()
    return await db[SCAN_NODE_BOOTSTRAP_TOKENS_COLLECTION].find_one_and_update(
        {
            "token_digest": token_digest,
            "used_at": {"$exists": False},
            "expires_at": {"$gt": now},
        },
        {"$set": {"used_at": now}},
        return_document=ReturnDocument.AFTER,
    )


async def get_active_bootstrap(
    db: AsyncIOMotorDatabase,
    *,
    token_digest: str,
) -> dict[str, Any] | None:
    return await db[SCAN_NODE_BOOTSTRAP_TOKENS_COLLECTION].find_one(
        {
            "token_digest": token_digest,
            "used_at": {"$exists": False},
            "expires_at": {"$gt": _now()},
        },
        {"_id": 0},
    )


async def register_node(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    bootstrap_id: str,
    display_name: str,
    token_digest: str,
    token_secret: str,
    identity_fingerprint: str,
    capabilities: dict[str, str],
    capacity: dict[str, int],
    labels: dict[str, str],
    version: str,
) -> dict[str, Any]:
    now = _now()
    doc = {
        "node_id": node_id,
        "bootstrap_id": bootstrap_id,
        "display_name": display_name,
        "token_digest": token_digest,
        "token_secret": encrypt_value(token_secret),
        "identity_fingerprint": identity_fingerprint,
        "status": "online",
        "capabilities": capabilities,
        "capacity": capacity,
        "usage": {},
        "labels": labels,
        "version": version,
        "last_heartbeat_at": now,
        "registered_at": now,
        "updated_at": now,
    }
    await db[SCAN_NODES_COLLECTION].insert_one(doc)
    return _public_node(doc) or {}


async def get_node_signing_secret(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
) -> tuple[dict[str, Any], str] | None:
    doc = await db[SCAN_NODES_COLLECTION].find_one(
        {"node_id": node_id, "status": {"$ne": "disabled"}},
        {"_id": 0},
    )
    if not doc:
        return None
    secret = str(decrypt_value(doc.get("token_secret")) or "")
    if not secret:
        return None
    return doc, secret


async def consume_request_nonce(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    nonce: str,
    expires_at: datetime,
) -> bool:
    try:
        await db[SCAN_NODE_NONCES_COLLECTION].insert_one(
            {
                "node_id": node_id,
                "nonce": nonce,
                "expires_at": expires_at,
                "created_at": _now(),
            }
        )
        return True
    except DuplicateKeyError:
        return False


async def get_node(
    db: AsyncIOMotorDatabase,
    node_id: str,
    *,
    public: bool = True,
) -> dict[str, Any] | None:
    doc = await db[SCAN_NODES_COLLECTION].find_one(
        {"node_id": node_id},
        {"_id": 0},
    )
    return _public_node(doc) if public else doc


async def list_nodes(
    db: AsyncIOMotorDatabase,
    *,
    status: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    cursor = (
        db[SCAN_NODES_COLLECTION]
        .find(query, {"_id": 0, "token_digest": 0, "token_secret": 0, "bootstrap_id": 0})
        .sort([("status", ASCENDING), ("last_heartbeat_at", DESCENDING)])
        .limit(max(1, min(limit, 2000)))
    )
    return [doc async for doc in cursor]


async def heartbeat(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    usage: dict[str, int],
    capabilities: dict[str, str] | None = None,
    capacity: dict[str, int] | None = None,
    version: str = "",
) -> dict[str, Any] | None:
    current = await get_node(db, node_id, public=False)
    if not current or current.get("status") == "disabled":
        return None
    now = _now()
    fields: dict[str, Any] = {
        "usage": usage,
        "last_heartbeat_at": now,
        "updated_at": now,
    }
    if current.get("status") == "offline":
        fields["status"] = "online"
    if capabilities is not None:
        fields["capabilities"] = capabilities
    if capacity is not None:
        fields["capacity"] = capacity
    if version:
        fields["version"] = version
    doc = await db[SCAN_NODES_COLLECTION].find_one_and_update(
        {"node_id": node_id, "status": {"$ne": "disabled"}},
        {"$set": fields},
        projection={"_id": 0, "token_digest": 0, "token_secret": 0, "bootstrap_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return doc


async def set_status(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    status: str,
) -> dict[str, Any] | None:
    return await db[SCAN_NODES_COLLECTION].find_one_and_update(
        {"node_id": node_id},
        {"$set": {"status": status, "updated_at": _now()}},
        projection={"_id": 0, "token_digest": 0, "token_secret": 0, "bootstrap_id": 0},
        return_document=ReturnDocument.AFTER,
    )


async def rotate_token(
    db: AsyncIOMotorDatabase,
    *,
    node_id: str,
    token_digest: str,
    token_secret: str,
    identity_fingerprint: str,
) -> bool:
    result = await db[SCAN_NODES_COLLECTION].update_one(
        {"node_id": node_id},
        {
            "$set": {
                "token_digest": token_digest,
                "token_secret": encrypt_value(token_secret),
                "identity_fingerprint": identity_fingerprint,
                "updated_at": _now(),
            }
        },
    )
    return bool(result.matched_count)


async def mark_stale_offline(
    db: AsyncIOMotorDatabase,
    *,
    stale_seconds: int = 90,
) -> int:
    cutoff = _now() - timedelta(seconds=max(30, stale_seconds))
    result = await db[SCAN_NODES_COLLECTION].update_many(
        {"status": "online", "last_heartbeat_at": {"$lt": cutoff}},
        {"$set": {"status": "offline", "updated_at": _now()}},
    )
    return int(result.modified_count)


async def has_eligible_node(
    db: AsyncIOMotorDatabase,
    *,
    capability: str,
    max_heartbeat_age_seconds: int = 90,
) -> bool:
    cutoff = _now() - timedelta(seconds=max(30, max_heartbeat_age_seconds))
    doc = await db[SCAN_NODES_COLLECTION].find_one(
        {
            "status": "online",
            f"capabilities.{capability}": {"$exists": True},
            "last_heartbeat_at": {"$gte": cutoff},
        },
        {"_id": 1},
    )
    return doc is not None


async def get_stats(db: AsyncIOMotorDatabase) -> dict[str, int]:
    rows = await db[SCAN_NODES_COLLECTION].aggregate(
        [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    ).to_list(length=None)
    result = {"total": 0, "online": 0, "draining": 0, "offline": 0, "disabled": 0}
    for row in rows:
        status = str(row.get("_id") or "offline")
        count = int(row.get("count") or 0)
        result["total"] += count
        result[status] = count
    return result
