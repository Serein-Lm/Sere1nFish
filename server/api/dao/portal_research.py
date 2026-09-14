"""Recoverable portal evidence ledger inside the existing task checkpoint."""
from datetime import datetime, timezone
import hashlib
from api.db.collections import TASKS_COLLECTION, SOURCE_DOCUMENT_LINKS_COLLECTION, SOURCE_DOCUMENTS_COLLECTION

async def load_checkpoint(db, task_id: str) -> dict:
    task = await db[TASKS_COLLECTION].find_one({"task_id": task_id}, {"checkpoint.portal_research": 1})
    return ((task or {}).get("checkpoint") or {}).get("portal_research") or {}

async def save_page(db, task_id: str, page: dict) -> None:
    key = hashlib.sha256(page["url"].encode()).hexdigest()[:32]
    await db[TASKS_COLLECTION].update_one({"task_id": task_id}, {"$set": {
        "checkpoint.portal_research.pages." + key: page,
        "checkpoint.portal_research.updated_at": datetime.now(timezone.utc),
    }})

async def save_result(db, task_id: str, data: dict) -> None:
    await db[TASKS_COLLECTION].update_one({"task_id": task_id}, {"$set": {
        "checkpoint.portal_research.result": data,
        "checkpoint.portal_research.updated_at": datetime.now(timezone.utc),
    }})

async def source_candidates(db, project_id: str, target_id: str) -> list[dict]:
    # Discovery hints only; the Agent must read a page before citing its facts.
    ids = await db[SOURCE_DOCUMENT_LINKS_COLLECTION].distinct("document_id", {"project_id": project_id, "target_id": target_id})
    return await db[SOURCE_DOCUMENTS_COLLECTION].find({"document_id": {"$in": ids}},
        {"_id": 0, "canonical_url": 1, "title": 1}).sort("last_seen_at", -1).limit(200).to_list(200)
