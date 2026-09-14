"""Immutable mobile increments, independent of mutable collection records."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db.collections import MOBILE_INCREMENTAL_EVENTS_COLLECTION


async def ensure_indexes(db) -> None:
    collection = db[MOBILE_INCREMENTAL_EVENTS_COLLECTION]
    await collection.create_index("event_id", unique=True)
    await collection.create_index([("project_id", 1), ("detected_at", -1)])
    await collection.create_index([("detected_at", -1), ("event_id", -1)])
    await collection.create_index([("run_task_id", 1), ("kind", 1)])


async def insert_event(db, event: dict) -> bool:
    try:
        result = await db[MOBILE_INCREMENTAL_EVENTS_COLLECTION].update_one(
            {"event_id": event["event_id"]}, {"$setOnInsert": event}, upsert=True,
        )
    except DuplicateKeyError:
        return False
    return result.upserted_id is not None


async def claim_delivery(db, event_id: str) -> dict | None:
    now = datetime.now(timezone.utc)
    return await db[MOBILE_INCREMENTAL_EVENTS_COLLECTION].find_one_and_update(
        {"event_id": event_id, "$or": [
            {"delivery_status": {"$in": ["pending", "failed"]}},
            {"delivery_status": "sending", "delivery_lease_until": {"$lt": now}},
        ]},
        {"$set": {"delivery_status": "sending", "delivery_lease_until": now + timedelta(minutes=5)}},
        return_document=ReturnDocument.AFTER, projection={"_id": 0},
    )


async def finish_delivery(db, event_id: str, *, status: str) -> None:
    await db[MOBILE_INCREMENTAL_EVENTS_COLLECTION].update_one(
        {"event_id": event_id},
        {"$set": {"delivery_status": status}, "$unset": {"delivery_lease_until": ""}},
    )


async def counts(db, query: dict) -> dict:
    rows = await db[MOBILE_INCREMENTAL_EVENTS_COLLECTION].aggregate([
        {"$match": query}, {"$group": {"_id": "$kind", "count": {"$sum": 1}}},
    ]).to_list(None)
    return {kind: next((row["count"] for row in rows if row["_id"] == kind), 0) for kind in ("new", "changed")}


async def feed(db, *, project_id: str = "", after: datetime | None = None, limit: int = 50) -> dict:
    now = datetime.now(timezone.utc)
    query: dict = {"detected_at": {"$gte": now - timedelta(days=7), "$lte": now}}
    if project_id:
        query["project_id"] = project_id
    unread_query = dict(query)
    if after:
        unread_query["detected_at"] = {**query["detected_at"], "$gt": after}
    collection = db[MOBILE_INCREMENTAL_EVENTS_COLLECTION]
    items, total, unread = await asyncio.gather(
        collection.find(query, {"_id": 0, "delivery_lease_until": 0}).sort([("detected_at", -1), ("event_id", -1)]).limit(limit).to_list(None),
        counts(db, query), counts(db, unread_query),
    )
    return {"items": items, "new_count": total["new"], "changed_count": total["changed"],
            "unread_count": sum(unread.values()), "generated_at": now, "days": 7}
