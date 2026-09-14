"""Atomic persona version outbox, drained to immutable analytical snapshots.

MongoDB standalone deployments cannot use multi-collection transactions. The
profile update and pre/post snapshots therefore commit in one document update;
the append-only version collection is an idempotent projection of that outbox.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pymongo import ReturnDocument, UpdateOne
from api.db.collections import PERSONS_COLLECTION, PERSON_PROFILE_VERSIONS_COLLECTION as VERSIONS

PENDING = "_pending_profile_versions"


def _snapshot() -> dict:
    return {"$arrayToObject": {"$filter": {"input": {"$objectToArray": "$$ROOT"}, "as": "field", "cond": {"$not": [{"$in": ["$$field.k", ["_id", PENDING]]}]}}}}


def update_pipeline(update: dict) -> list[dict]:
    fields = {key: {"$literal": value} for key, value in update.get("$set", {}).items()}
    for key, value in update.get("$setOnInsert", {}).items():
        fields[key] = {"$ifNull": ["$" + key, {"$literal": value}]}
    for key, value in update.get("$inc", {}).items():
        fields[key] = {"$add": [{"$ifNull": ["$" + key, 0]}, value]}
    for key, value in update.get("$addToSet", {}).items():
        values = value["$each"] if isinstance(value, dict) and "$each" in value else [value]
        fields[key] = {"$setUnion": [{"$ifNull": ["$" + key, []]}, {"$literal": values}]}
    for key, value in update.get("$push", {}).items():
        fields[key] = {"$concatArrays": [{"$ifNull": ["$" + key, []]}, {"$literal": [value]}]}
    before = {"$cond": [{"$gt": [{"$ifNull": ["$profile_version", 0]}, 0]}, [_snapshot()], []]}
    return [
        {"$set": {PENDING: {"$concatArrays": [{"$ifNull": ["$" + PENDING, []]}, before]}}},
        {"$set": fields},
        {"$set": {PENDING: {"$concatArrays": ["$" + PENDING, [_snapshot()]]}}},
    ]


async def flush(db, person: dict) -> None:
    snapshots = {int(item["profile_version"]): item for item in person.get(PENDING, []) if item.get("profile_version")}
    if not snapshots:
        return
    instant = datetime.now(timezone.utc)
    await db[VERSIONS].bulk_write([UpdateOne(
        {"person_id": person["person_id"], "profile_version": version},
        {"$setOnInsert": {"person_id": person["person_id"], "profile_version": version, "profile": payload, "archived_at": instant, "effective_at": payload.get("updated_at") or payload.get("created_at"), "source": "profile_version_outbox"}}, upsert=True,
    ) for version, payload in snapshots.items()], ordered=False)
    await db[PERSONS_COLLECTION].update_one({"person_id": person["person_id"]}, {"$pull": {PENDING: {"profile_version": {"$in": list(snapshots)}}}})


async def update_with_version(db, person_id: str, update: dict) -> dict:
    person = await db[PERSONS_COLLECTION].find_one_and_update({"person_id": person_id}, update_pipeline(update), upsert=True, return_document=ReturnDocument.AFTER)
    await flush(db, person)
    return {key: value for key, value in person.items() if key not in {"_id", PENDING}}


async def ensure_indexes(db) -> None:
    await db[VERSIONS].create_index([("person_id", 1), ("profile_version", 1)], unique=True)
    await db[VERSIONS].create_index([("profile.industry_code", 1), ("effective_at", -1)])


async def recover_and_backfill(db) -> int:
    """Archive surviving current profiles; never invent missing historical versions."""
    count = 0
    async for person in db[PERSONS_COLLECTION].find({}, {"_id": 0}):
        await flush(db, person)
        profile = {key: value for key, value in person.items() if key != PENDING}
        version = int(profile.get("profile_version") or 1)
        result = await db[VERSIONS].update_one({"person_id": person["person_id"], "profile_version": version}, {"$setOnInsert": {"person_id": person["person_id"], "profile_version": version, "profile": profile, "archived_at": datetime.now(timezone.utc), "effective_at": profile.get("updated_at") or profile.get("created_at"), "source": "existing_current_profile"}}, upsert=True)
        count += int(result.upserted_id is not None)
    return count


async def list_versions(db, person_id: str, skip: int = 0, limit: int = 20) -> dict:
    query = {"person_id": person_id}
    items = await db[VERSIONS].find(query, {"_id": 0}).sort("profile_version", -1).skip(skip).limit(limit).to_list(limit)
    return {"items": items, "total": await db[VERSIONS].count_documents(query), "skip": skip, "limit": limit}
