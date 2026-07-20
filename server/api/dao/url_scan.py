"""Durable URL scan result persistence and restart checkpoints."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.collections import URL_SCAN_RESULTS_COLLECTION
from api.utils.url_identity import endpoint_identity


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
    finding_count: int = 0,
    high_risk_count: int = 0,
    max_attention_score: int = 0,
    intro: dict[str, Any] | None = None,
    screenshot_object_id: str = "",
    screenshot_url: str = "",
    excluded: bool = False,
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
        "retryable": False,
        "error": str(error or "")[:2_000] or None,
        "has_findings": bool(has_findings),
        "short_circuited": bool(short_circuited),
        "classification": str(classification or ""),
        "endpoint_key": endpoint_identity(url),
        "finding_count": max(0, int(finding_count or 0)),
        "high_risk_count": max(0, int(high_risk_count or 0)),
        "max_attention_score": max(0, min(100, int(max_attention_score or 0))),
        "intro": dict(intro or {}),
        "screenshot_object_id": str(screenshot_object_id or ""),
        "screenshot_url": str(screenshot_url or ""),
        "excluded": bool(excluded),
        "updated_at": now,
        "completed_at": now,
    }
    await db[URL_SCAN_RESULTS_COLLECTION].update_one(
        {"result_id": stable_id},
        {"$set": fields, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return fields


async def upsert_retryable_result(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    project_id: str,
    url: str,
    source: str,
    target_id: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Persist an infrastructure failure without treating the URL as completed."""
    now = _now()
    stable_id = result_id(task_id, url)
    entry = {
        "recorded_at": now,
        "error": str(error or "")[:2_000],
    }
    fields: dict[str, Any] = {
        "result_id": stable_id,
        "task_id": task_id,
        "project_id": project_id,
        "target_id": target_id,
        "source": source,
        "url": url,
        "success": False,
        "terminal": False,
        "retryable": True,
        "error": entry["error"] or None,
        "updated_at": now,
    }
    await db[URL_SCAN_RESULTS_COLLECTION].update_one(
        {"result_id": stable_id},
        {
            "$set": fields,
            "$unset": {"completed_at": ""},
            "$setOnInsert": {"created_at": now},
            "$push": {
                "attempt_errors": {
                    "$each": [entry],
                    "$slice": -10,
                }
            },
        },
        upsert=True,
    )
    return fields


def _terminal_result_filter() -> dict[str, Any]:
    return {
        "$or": [
            {"terminal": True},
            {
                "terminal": {"$exists": False},
                "success": {"$exists": True},
            },
        ]
    }


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
            **_terminal_result_filter(),
        },
        {"_id": 0, "url": 1},
    )
    return {str(item.get("url") or "") async for item in cursor}


async def retryable_task_ids(
    db: AsyncIOMotorDatabase,
    *,
    task_ids: list[str] | set[str] | tuple[str, ...],
) -> set[str]:
    """Return child scan task IDs that still contain retryable URL rows."""
    normalized = sorted(
        {
            str(task_id or "").strip()
            for task_id in task_ids
            if str(task_id or "").strip()
        }
    )
    if not normalized:
        return set()
    values = await db[URL_SCAN_RESULTS_COLLECTION].distinct(
        "task_id",
        {
            "task_id": {"$in": normalized},
            "terminal": False,
            "retryable": True,
        },
    )
    return {str(value) for value in values if str(value or "").strip()}


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
        **_terminal_result_filter(),
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
