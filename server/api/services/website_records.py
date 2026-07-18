"""Unified project website analysis records.

Company scans persist execution status in ``url_scan_results`` and structured
evidence in ``findings``. Legacy one-off Web Tagging records remain readable
through the same project surface.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.dao import web_tagging as web_tagging_dao
from api.db.collections import FINDINGS_COLLECTION, URL_SCAN_RESULTS_COLLECTION


def _url_key(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


_MIN_DATETIME = datetime.min.replace(tzinfo=timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[URL_SCAN_RESULTS_COLLECTION].create_index(
        [("project_id", 1), ("source", 1), ("target_id", 1)]
    )


def _created_at(doc: dict[str, Any]) -> datetime:
    """Return a comparable, timezone-aware creation time for mixed legacy data."""
    value = doc.get("created_at")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    object_id = doc.get("_id")
    if isinstance(object_id, ObjectId):
        return object_id.generation_time
    return _MIN_DATETIME


def _score(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _record_sort_key(record: dict[str, Any]) -> tuple[int, float, datetime, str]:
    """Sort findings before safe/empty records, then by highest score and time."""
    data = record.get("data") or {}
    findings = data.get("findings") or []
    scores = [
        _score(item.get("attention_score"))
        for item in findings
        if isinstance(item, dict)
    ]
    has_findings = bool(findings)
    return (
        1 if has_findings else 0,
        max(scores, default=0.0),
        _created_at(record),
        str(record.get("_id") or record.get("id") or ""),
    )


def _adapt_url_scan_record(
    scan: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_findings = sorted(
        findings,
        key=lambda item: _score(item.get("attention_score")),
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
    target_id: str = "",
) -> tuple[list[dict[str, Any]], int]:
    query = {"project_id": project_id, "source": "web_tagging"}
    if target_id:
        query["target_id"] = target_id
    collection = db[URL_SCAN_RESULTS_COLLECTION]
    total = await collection.count_documents(query)
    scans = await collection.find(query).to_list(max(1, total))
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
    target_id: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """List URL scans and legacy Web Tagging records in one sorted page.

    The two sources live in different collections, so pagination must happen
    after their records are adapted, merged, and globally sorted.
    """
    bounded_limit = max(1, min(int(limit or 50), 200))
    bounded_skip = max(0, int(skip or 0))
    selected_target_id = str(target_id or "").strip()

    url_records, url_total = await _list_url_scan_records(
        db,
        project_id=project_id,
        target_id=selected_target_id,
    )
    # Each source is score-sorted. Records below this source-local top K cannot
    # enter the merged top K, so old records do not need to be fully loaded.
    candidate_limit = bounded_skip + bounded_limit
    legacy_records, legacy_total = await web_tagging_dao.list_web_tagging_results(
        db,
        project_id=project_id,
        limit=candidate_limit,
        skip=0,
        source="web_tagging",
        target_id=selected_target_id,
    )

    records = [*url_records, *legacy_records]
    records.sort(key=_record_sort_key, reverse=True)
    return records[bounded_skip : bounded_skip + bounded_limit], url_total + legacy_total
