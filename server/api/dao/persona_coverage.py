"""Durable industry coverage jobs and immutable source-backed organization facts."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument, UpdateOne
from api.db.collections import PERSONA_COVERAGE_JOBS_COLLECTION as JOBS, INDUSTRY_ORGANIZATION_FACTS_COLLECTION as FACTS, PERSONS_COLLECTION


def now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(db) -> None:
    await db[JOBS].create_index("industry_code", unique=True)
    await db[JOBS].create_index("job_id", unique=True)
    await db[JOBS].create_index([("status", 1), ("next_attempt_at", 1), ("priority", 1)])
    await db[FACTS].create_index("fact_id", unique=True)
    await db[FACTS].create_index([("industry_code", 1), ("target_id", 1), ("captured_at", -1)])
    await db[FACTS].create_index("source_document_version_id")
    await db[PERSONS_COLLECTION].create_index("industry_code", sparse=True)


async def enqueue(db, divisions: list[dict], minimum: int, *, generation_mode: str = "context") -> dict:
    operations = []
    for priority, division in enumerate(divisions):
        fields = {"industry_name": division["name"], "sector_code": division["sector_code"], "minimum_personas": minimum, "generation_mode": generation_mode, "priority": priority, "updated_at": now()}
        operations.append(UpdateOne({"industry_code": division["code"]}, {"$set": fields, "$setOnInsert": {"job_id": "industry_" + division["code"], "status": "queued", "attempts": 0, "next_attempt_at": now(), "created_at": now(), "stage": "queued", "gaps": [], "person_ids": [], "fact_ids": []}}, upsert=True))
    if operations:
        await db[JOBS].bulk_write(operations, ordered=False)
        await db[JOBS].update_many({"industry_code": {"$in": [item["code"] for item in divisions]}, "status": {"$in": ["failed", "retry", "needs_sources", "completed", "queued"]}}, {"$set": {"status": "queued", "stage": "queued", "attempts": 0, "next_attempt_at": now(), "updated_at": now(), "errors": [], "message": "等待自动补齐完整上下文" if generation_mode == "context" else "等待行业资料研究"}})
    return {"queued_industries": len(divisions), "minimum_personas": minimum}


async def claim(db, owner: str) -> dict | None:
    instant = now()
    return await db[JOBS].find_one_and_update({"$or": [
        {"status": {"$in": ["queued", "retry"]}, "next_attempt_at": {"$lte": instant}},
        {"status": "running", "lease_until": {"$lt": instant}},
    ]}, {"$set": {"status": "running", "lease_owner": owner, "lease_until": instant + timedelta(minutes=5), "started_at": instant, "updated_at": instant}, "$inc": {"attempts": 1}}, sort=[("priority", 1)], projection={"_id": 0}, return_document=ReturnDocument.AFTER)


async def requeue_sources_due(db) -> None:
    await db[JOBS].update_many({"status": "needs_sources", "next_attempt_at": {"$lte": now()}}, {"$set": {"status": "queued", "attempts": 0, "stage": "queued", "updated_at": now()}})


async def update_job(db, job_id: str, owner: str, **fields) -> bool:
    fields["updated_at"] = now()
    result = await db[JOBS].update_one({"job_id": job_id, "lease_owner": owner, "status": "running"}, {"$set": fields})
    return result.matched_count == 1


async def heartbeat(db, job_id: str, owner: str) -> bool:
    return await update_job(db, job_id, owner, lease_until=now() + timedelta(minutes=5))


async def save_facts(db, facts: list[dict]) -> list[str]:
    if facts:
        await db[FACTS].bulk_write([UpdateOne({"fact_id": fact["fact_id"]}, {"$setOnInsert": fact}, upsert=True) for fact in facts], ordered=False)
    return [fact["fact_id"] for fact in facts]


async def classify_persons(db, person_ids: list[str], *, industry_code: str, sector_code: str, job_id: str, method: str = "scoped_industry_research") -> None:
    from api.dao.person_versions import update_with_version
    candidates = await db[PERSONS_COLLECTION].find({"person_id": {"$in": person_ids}, "is_fictional": True, "industry_code": {"$ne": industry_code}}, {"person_id": 1}).to_list(None)
    await asyncio.gather(*(update_with_version(db, item["person_id"], {"$set": {"industry_code": industry_code, "industry_sector_code": sector_code, "updated_at": now(), "industry_classification": {"standard": "GB/T 4754-2017", "method": method, "job_id": job_id, "classified_at": now()}}, "$inc": {"profile_version": 1}}) for item in candidates))


async def unclassified_people(db) -> list[dict]:
    return await db[PERSONS_COLLECTION].find({"is_fictional": True, "industry_code": {"$in": [None, ""]}}, {"_id": 0, "person_id": 1, "industry": 1}).to_list(None)


async def snapshots(db) -> tuple:
    return await asyncio.gather(
        db[JOBS].find({}, {"_id": 0, "lease_owner": 0}).to_list(None),
        db[PERSONS_COLLECTION].find({"is_fictional": True}, {"_id": 0, "person_id": 1, "industry": 1, "industry_code": 1, "industry_sector_code": 1, "summary": 1, "company": 1, "position": 1, "context_complete": 1, "context_review": 1, "source_urls": 1, "research_evidence": 1, "last_researched_at": 1}).to_list(None),
        db[FACTS].aggregate([{"$group": {"_id": "$industry_code", "organizations": {"$addToSet": "$target_id"}, "sources": {"$addToSet": "$source_document_id"}, "phone_count": {"$sum": {"$cond": [{"$ne": ["$office_phone", ""]}, 1, 0]}}, "fact_count": {"$sum": 1}}}]).to_list(None),
    )


async def list_facts(db, industry_code: str = "", skip: int = 0, limit: int = 20) -> dict:
    query = {"industry_code": industry_code} if industry_code else {}
    total, items = await asyncio.gather(db[FACTS].count_documents(query), db[FACTS].find(query, {"_id": 0}).sort([("captured_at", -1), ("fact_id", 1)]).skip(skip).limit(limit).to_list(limit))
    return {"items": items, "total": total, "skip": skip, "limit": limit}


async def industry_people(db, industry_code: str) -> list[dict]:
    return await db[PERSONS_COLLECTION].find({"is_fictional": True, "industry_code": industry_code}, {"_id": 0, "person_id": 1, "industry": 1, "summary": 1, "company": 1, "position": 1, "context_complete": 1, "context_review": 1, "source_urls": 1, "research_evidence": 1}).to_list(None)
