"""Persistence for resumable official-website document collection."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from api.db.collections import (
    WEBSITE_CRAWL_PAGES_COLLECTION,
    WEBSITE_CRAWL_TASKS_COLLECTION,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def page_id_for_url(crawl_task_id: str, canonical_url: str) -> str:
    raw = f"{crawl_task_id}:{canonical_url}".encode("utf-8")
    return "wcp_" + hashlib.sha256(raw).hexdigest()[:24]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    tasks = db[WEBSITE_CRAWL_TASKS_COLLECTION]
    await tasks.create_index("crawl_task_id", unique=True)
    await tasks.create_index([("project_id", 1), ("target_id", 1), ("updated_at", -1)])
    await tasks.create_index([("status", 1), ("updated_at", -1)])

    pages = db[WEBSITE_CRAWL_PAGES_COLLECTION]
    await pages.create_index("page_id", unique=True)
    await pages.create_index(
        [("crawl_task_id", 1), ("status", 1), ("priority", -1), ("depth", 1)]
    )
    await pages.create_index([("project_id", 1), ("target_id", 1), ("updated_at", -1)])
    await pages.create_index("document_id", sparse=True)


async def begin_task(
    db: AsyncIOMotorDatabase,
    *,
    crawl_task_id: str,
    parent_task_id: str,
    project_id: str,
    target_id: str,
    target_name: str,
    seeds: list[str],
    root_domains: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    now = _now()
    await db[WEBSITE_CRAWL_TASKS_COLLECTION].update_one(
        {"crawl_task_id": crawl_task_id},
        {
            "$set": {
                "parent_task_id": parent_task_id,
                "project_id": project_id,
                "target_id": target_id,
                "target_name": target_name,
                "seeds": seeds,
                "root_domains": root_domains,
                "config": config,
                "status": "running",
                "error": "",
                "updated_at": now,
                "heartbeat_at": now,
                "started_at": now,
            },
            "$unset": {"completed_at": ""},
            "$setOnInsert": {
                "crawl_task_id": crawl_task_id,
                "created_at": now,
            },
        },
        upsert=True,
    )
    await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_many(
        {"crawl_task_id": crawl_task_id},
        {"$set": {"attempts_in_run": 0}},
    )
    await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_many(
        {
            "crawl_task_id": crawl_task_id,
            "status": {
                "$in": ["fetching", "archiving", "error", "partial"]
            },
        },
        {
            "$set": {
                "status": "pending",
                "recovered_at": now,
                "updated_at": now,
            }
        },
    )
    await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_many(
        {
            "crawl_task_id": crawl_task_id,
            "depth": 0,
            "parent_url": "",
            "canonical_url": {"$nin": seeds},
            "status": {"$nin": ["archived", "discovered", "rejected"]},
        },
        {
            "$set": {
                "status": "superseded",
                "completed_at": now,
                "updated_at": now,
            }
        },
    )
    return await get_task(db, crawl_task_id) or {}


async def get_task(
    db: AsyncIOMotorDatabase,
    crawl_task_id: str,
) -> dict[str, Any] | None:
    return await db[WEBSITE_CRAWL_TASKS_COLLECTION].find_one(
        {"crawl_task_id": crawl_task_id},
        {"_id": 0},
    )


async def enqueue_pages(
    db: AsyncIOMotorDatabase,
    *,
    crawl_task_id: str,
    project_id: str,
    target_id: str,
    pages: list[dict[str, Any]],
) -> list[str]:
    """Insert unseen URLs and return the page IDs created by this call."""
    if not pages:
        return []
    now = _now()
    operations: list[UpdateOne] = []
    page_ids: list[str] = []
    for page in pages:
        canonical_url = str(page.get("canonical_url") or "").strip()
        if not canonical_url:
            continue
        page_id = page_id_for_url(crawl_task_id, canonical_url)
        page_ids.append(page_id)
        operations.append(
            UpdateOne(
                {"page_id": page_id},
                {
                    "$setOnInsert": {
                        "page_id": page_id,
                        "crawl_task_id": crawl_task_id,
                        "project_id": project_id,
                        "target_id": target_id,
                        "canonical_url": canonical_url,
                        "parent_url": str(page.get("parent_url") or ""),
                        "anchor_text": str(page.get("anchor_text") or "")[:500],
                        "kind": str(page.get("kind") or "index"),
                        "scope_relevant": bool(page.get("scope_relevant")),
                        "depth": max(0, int(page.get("depth") or 0)),
                        "priority": int(page.get("priority") or 0),
                        "status": "pending",
                        "attempts": 0,
                        "attempts_in_run": 0,
                        "created_at": now,
                        "updated_at": now,
                    }
                },
                upsert=True,
            )
        )
    if not operations:
        return []
    result = await db[WEBSITE_CRAWL_PAGES_COLLECTION].bulk_write(
        operations,
        ordered=False,
    )
    inserted_indexes = set((result.upserted_ids or {}).keys())
    return [page_ids[index] for index in sorted(inserted_indexes)]


async def list_pages(
    db: AsyncIOMotorDatabase,
    *,
    crawl_task_id: str,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"crawl_task_id": crawl_task_id}
    if statuses:
        query["status"] = {"$in": statuses}
    return await (
        db[WEBSITE_CRAWL_PAGES_COLLECTION]
        .find(query, {"_id": 0})
        .sort([("priority", -1), ("depth", 1), ("created_at", 1)])
        .to_list(None)
    )


async def mark_page_started(
    db: AsyncIOMotorDatabase,
    *,
    page_id: str,
    status: str,
) -> None:
    now = _now()
    await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_one(
        {"page_id": page_id},
        {
            "$set": {"status": status, "updated_at": now},
            "$inc": {"attempts": 1, "attempts_in_run": 1},
        },
    )


async def mark_page_retry(
    db: AsyncIOMotorDatabase,
    *,
    page_id: str,
    error: str,
) -> None:
    now = _now()
    await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_one(
        {"page_id": page_id},
        {
            "$set": {
                "status": "pending",
                "last_error": str(error or "")[:2_000],
                "last_failed_at": now,
                "updated_at": now,
            },
            "$unset": {"completed_at": "", "error": ""},
        },
    )


async def mark_page_terminal(
    db: AsyncIOMotorDatabase,
    *,
    page_id: str,
    status: str,
    fields: dict[str, Any] | None = None,
) -> None:
    now = _now()
    await db[WEBSITE_CRAWL_PAGES_COLLECTION].update_one(
        {"page_id": page_id},
        {
            "$set": {
                "status": status,
                **dict(fields or {}),
                "updated_at": now,
                "completed_at": now,
            }
        },
    )


async def heartbeat_task(
    db: AsyncIOMotorDatabase,
    *,
    crawl_task_id: str,
    counters: dict[str, Any],
) -> None:
    now = _now()
    await db[WEBSITE_CRAWL_TASKS_COLLECTION].update_one(
        {"crawl_task_id": crawl_task_id},
        {
            "$set": {
                "counters": counters,
                "heartbeat_at": now,
                "updated_at": now,
            }
        },
    )


async def finish_task(
    db: AsyncIOMotorDatabase,
    *,
    crawl_task_id: str,
    status: str,
    summary: dict[str, Any],
    error: str = "",
) -> dict[str, Any]:
    now = _now()
    await db[WEBSITE_CRAWL_TASKS_COLLECTION].update_one(
        {"crawl_task_id": crawl_task_id},
        {
            "$set": {
                "status": status,
                "summary": summary,
                "error": str(error or "")[:4_000],
                "updated_at": now,
                "completed_at": now,
            }
        },
    )
    return await get_task(db, crawl_task_id) or {"summary": summary, "status": status}


async def summarize_task(
    db: AsyncIOMotorDatabase,
    *,
    crawl_task_id: str,
) -> dict[str, Any]:
    task = await get_task(db, crawl_task_id) or {}
    rows = await db[WEBSITE_CRAWL_PAGES_COLLECTION].aggregate(
        [
            {"$match": {"crawl_task_id": crawl_task_id}},
            {
                "$group": {
                    "_id": {"status": "$status", "kind": "$kind"},
                    "count": {"$sum": 1},
                    "attachments": {"$sum": {"$ifNull": ["$attachment_count", 0]}},
                    "contacts": {"$sum": {"$ifNull": ["$contact_count", 0]}},
                }
            },
        ]
    ).to_list(None)
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    attachments = 0
    contacts = 0
    for row in rows:
        key = dict(row.get("_id") or {})
        status = str(key.get("status") or "unknown")
        kind = str(key.get("kind") or "unknown")
        count = int(row.get("count") or 0)
        by_status[status] = by_status.get(status, 0) + count
        by_kind[kind] = by_kind.get(kind, 0) + count
        attachments += int(row.get("attachments") or 0)
        contacts += int(row.get("contacts") or 0)
    total = sum(by_status.values())
    pending = sum(
        by_status.get(status, 0)
        for status in ("pending", "fetching", "archiving")
    )
    failed = by_status.get("error", 0)
    partial = by_status.get("partial", 0)
    return {
        "crawl_task_id": crawl_task_id,
        "status": str(task.get("status") or "unknown"),
        "total_pages": total,
        "pending_pages": pending,
        "failed_pages": failed,
        "archived_documents": by_status.get("archived", 0) + partial,
        "partial_documents": partial,
        "rejected_documents": by_status.get("rejected", 0),
        "attachments_archived": attachments,
        "contacts_found": contacts,
        "by_status": by_status,
        "by_kind": by_kind,
        "truncated": bool((task.get("summary") or {}).get("truncated")),
        "error": str(task.get("error") or ""),
    }


async def task_requires_retry(
    db: AsyncIOMotorDatabase,
    *,
    crawl_task_id: str,
) -> bool:
    task = await get_task(db, crawl_task_id)
    if not task:
        return False
    summary = await summarize_task(db, crawl_task_id=crawl_task_id)
    return bool(
        str(task.get("status") or "").lower()
        not in {"completed", "skipped", "disabled"}
        or int(summary.get("pending_pages") or 0)
        or int(summary.get("failed_pages") or 0)
        or int(summary.get("partial_documents") or 0)
        or bool(summary.get("truncated"))
    )
