"""Durable URL scan result persistence and restart checkpoints."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import URL_SCAN_RESULTS_COLLECTION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def result_id(task_id: str, url: str) -> str:
    raw = f"url-scan:{task_id}:{url}".encode("utf-8")
    return "usr_" + hashlib.sha1(raw).hexdigest()[:24]


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[URL_SCAN_RESULTS_COLLECTION]
    await collection.create_index("result_id", unique=True, sparse=True)
    await collection.create_index([("task_id", 1), ("url", 1)])
    await collection.create_index(
        [("task_id", 1), ("terminal", 1), ("success", 1)]
    )


async def upsert_terminal_result(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    project_id: str,
    url: str,
    success: bool,
    source: str,
    target_id: str = "",
    error: str = "",
    has_findings: bool = False,
    short_circuited: bool = False,
    classification: str = "",
) -> dict[str, Any]:
    """Persist one final URL outcome without duplicating it on restart."""
    now = _now()
    stable_id = result_id(task_id, url)
    fields: dict[str, Any] = {
        "result_id": stable_id,
        "task_id": task_id,
        "project_id": project_id,
        "target_id": target_id,
        "source": source,
        "url": url,
        "success": bool(success),
        "terminal": True,
        "error": str(error or "")[:2_000] or None,
        "has_findings": bool(has_findings),
        "short_circuited": bool(short_circuited),
        "classification": str(classification or ""),
        "updated_at": now,
        "completed_at": now,
    }
    await db[URL_SCAN_RESULTS_COLLECTION].update_one(
        {"result_id": stable_id},
        {"$set": fields, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return fields


async def completed_urls(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    urls: list[str],
) -> set[str]:
    """Return terminal URLs, including records written by the legacy batch writer."""
    if not task_id or not urls:
        return set()
    cursor = db[URL_SCAN_RESULTS_COLLECTION].find(
        {
            "task_id": task_id,
            "url": {"$in": urls},
            "$or": [
                {"terminal": True},
                {"success": {"$exists": True}},
            ],
        },
        {"_id": 0, "url": 1},
    )
    return {str(item.get("url") or "") async for item in cursor}


async def summarize_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    urls: list[str] | None = None,
) -> dict[str, int]:
    query: dict[str, Any] = {"task_id": task_id}
    if urls is not None:
        query["url"] = {"$in": urls}
    collection = db[URL_SCAN_RESULTS_COLLECTION]
    terminal_query = {
        **query,
        "$or": [{"terminal": True}, {"success": {"$exists": True}}],
    }
    rows = await collection.find(
        terminal_query,
        {"_id": 0, "url": 1, "success": 1, "short_circuited": 1},
    ).to_list(None)
    # Mixed legacy/new deployments can contain duplicate rows. Count URLs, not rows.
    by_url: dict[str, dict[str, Any]] = {}
    for row in rows:
        url = str(row.get("url") or "")
        if url:
            by_url[url] = row
    return {
        "processed": len(by_url),
        "succeeded": sum(1 for row in by_url.values() if row.get("success")),
        "failed": sum(1 for row in by_url.values() if not row.get("success")),
        "short_circuited": sum(
            1 for row in by_url.values() if row.get("short_circuited")
        ),
    }
