"""Unified project website analysis records.

Company scans persist execution status in ``url_scan_results`` and structured
evidence in ``findings``. Legacy one-off Web Tagging records remain readable
through the same project surface.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import web_tagging as web_tagging_dao
from api.db.collections import FINDINGS_COLLECTION, URL_SCAN_RESULTS_COLLECTION


def _url_key(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _created_at(doc: dict[str, Any]) -> datetime:
    value = doc.get("created_at")
    if isinstance(value, datetime):
        return value
    object_id = doc.get("_id")
    if isinstance(object_id, ObjectId):
        return object_id.generation_time
    return datetime.now(timezone.utc)


def _adapt_url_scan_record(
    scan: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_findings = sorted(
        findings,
        key=lambda item: int(item.get("attention_score") or 0),
        reverse=True,
    )
    lead = ordered_findings[0] if ordered_findings else {}
    url = str(scan.get("url") or "")
    error = str(scan.get("error") or "").strip()
    success = bool(scan.get("success"))
    return {
        "_id": scan.get("_id"),
        "project_id": str(scan.get("project_id") or ""),
        "url": url,
        "task_id": str(scan.get("task_id") or ""),
        "source": str(scan.get("source") or "web_tagging"),
        "target_id": str(scan.get("target_id") or ""),
        "created_at": _created_at(scan),
        "data": {
            "intro": {
                "url": url,
                "final_url": str(lead.get("source_url") or url),
                "domain": str(lead.get("domain") or ""),
                "site_name": str(lead.get("site_name") or ""),
                "entity_name": str(
                    lead.get("entity_name") or lead.get("party_name") or ""
                ),
                "summary": str(lead.get("summary") or ""),
            },
            "has_findings": bool(ordered_findings),
            "no_findings_reason": (
                error
                if error
                else None
                if ordered_findings
                else "扫描完成，未发现符合条件的信息"
                if success
                else "网站扫描未成功完成"
            ),
            "findings": ordered_findings,
        },
    }


async def _list_url_scan_records(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    skip: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    query = {"project_id": project_id, "source": "web_tagging"}
    collection = db[URL_SCAN_RESULTS_COLLECTION]
    total = await collection.count_documents(query)
    cursor = (
        collection.find(query)
        .sort("_id", -1)
        .skip(max(0, skip))
        .limit(max(1, limit))
    )
    scans = await cursor.to_list(max(1, limit))
    if not scans:
        return [], total

    task_ids = list(
        dict.fromkeys(str(item.get("task_id") or "") for item in scans)
    )
    finding_cursor = db[FINDINGS_COLLECTION].find(
        {
            "project_id": project_id,
            "source": "web_tagging",
            "task_id": {"$in": task_ids},
        },
        {"_id": 0},
    )
    findings_by_record: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    async for finding in finding_cursor:
        key = (
            str(finding.get("task_id") or ""),
            _url_key(finding.get("source_url") or finding.get("url")),
        )
        findings_by_record[key].append(finding)

    records = []
    for scan in scans:
        key = (str(scan.get("task_id") or ""), _url_key(scan.get("url")))
        records.append(_adapt_url_scan_record(scan, findings_by_record.get(key, [])))
    return records, total


async def list_website_records(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """List current URL scans first, then legacy Web Tagging records."""
    bounded_limit = max(1, min(int(limit or 50), 200))
    bounded_skip = max(0, int(skip or 0))

    _, url_total = await _list_url_scan_records(
        db,
        project_id=project_id,
        skip=0,
        limit=1,
    )
    _, legacy_total = await web_tagging_dao.list_web_tagging_results(
        db,
        project_id=project_id,
        limit=1,
        skip=0,
        source="web_tagging",
    )

    items: list[dict[str, Any]] = []
    remaining = bounded_limit
    if bounded_skip < url_total:
        current, _ = await _list_url_scan_records(
            db,
            project_id=project_id,
            skip=bounded_skip,
            limit=min(remaining, url_total - bounded_skip),
        )
        items.extend(current)
        remaining -= len(current)
        legacy_skip = 0
    else:
        legacy_skip = bounded_skip - url_total

    if remaining > 0 and legacy_skip < legacy_total:
        legacy, _ = await web_tagging_dao.list_web_tagging_results(
            db,
            project_id=project_id,
            limit=remaining,
            skip=legacy_skip,
            source="web_tagging",
        )
        items.extend(legacy)

    return items, url_total + legacy_total
