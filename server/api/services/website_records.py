"""Unified, deduplicated project website records."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from api.dao import web_tagging as web_tagging_dao
from api.db.collections import FINDINGS_COLLECTION, URL_SCAN_RESULTS_COLLECTION
from api.services.site_relevance import classify_generic_surface
from api.utils.url_identity import endpoint_identity, prefer_https_url


_MIN_DATETIME = datetime.min.replace(tzinfo=timezone.utc)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    collection = db[URL_SCAN_RESULTS_COLLECTION]
    await collection.create_index(
        [("project_id", 1), ("source", 1), ("target_id", 1)]
    )
    await collection.create_index(
        [
            ("project_id", 1),
            ("source", 1),
            ("target_id", 1),
            ("excluded", 1),
            ("high_risk_count", -1),
            ("finding_count", -1),
        ]
    )
    await collection.create_index(
        [("project_id", 1), ("source", 1), ("target_id", 1), ("endpoint_key", 1)]
    )
    pending = collection.find(
        {
            "source": "web_tagging",
            "$or": [
                {"endpoint_key": {"$exists": False}},
                {"endpoint_key": ""},
            ],
        },
        {"_id": 1, "url": 1},
    )
    batch: list[UpdateOne] = []
    async for doc in pending:
        key = endpoint_identity(str(doc.get("url") or ""))
        if key:
            batch.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"endpoint_key": key}}))
        if len(batch) >= 500:
            await collection.bulk_write(batch, ordered=False)
            batch.clear()
    if batch:
        await collection.bulk_write(batch, ordered=False)


def _created_at(doc: dict[str, Any]) -> datetime:
    value = doc.get("updated_at") or doc.get("created_at")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    object_id = doc.get("_id")
    return object_id.generation_time if isinstance(object_id, ObjectId) else _MIN_DATETIME


def _score(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _record_metrics(record: dict[str, Any]) -> tuple[int, int, float]:
    data = record.get("data") or {}
    findings = [item for item in data.get("findings") or [] if isinstance(item, dict)]
    scores = [_score(item.get("attention_score")) for item in findings]
    high_risk = sum(score >= 70 for score in scores)
    return high_risk, len(findings), max(scores, default=0.0)


def _record_sort_key(record: dict[str, Any]) -> tuple[int, int, float, datetime, str]:
    high_risk, finding_count, max_score = _record_metrics(record)
    return (
        high_risk,
        finding_count,
        max_score,
        _created_at(record),
        str(record.get("_id") or record.get("id") or ""),
    )


def _is_excluded(record: dict[str, Any]) -> bool:
    if record.get("excluded"):
        return True
    data = record.get("data") or {}
    intro = data.get("intro") or record.get("intro") or {}
    return bool(
        classify_generic_surface(
            record.get("url"),
            intro.get("site_name"),
            intro.get("entity_name"),
            intro.get("summary"),
        )
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
    stored_intro = dict(scan.get("intro") or {})
    url = str(scan.get("url") or "")
    intro = {
        "url": url,
        "final_url": str(stored_intro.get("final_url") or lead.get("source_url") or url),
        "domain": str(stored_intro.get("domain") or lead.get("domain") or ""),
        "site_name": str(stored_intro.get("site_name") or lead.get("site_name") or ""),
        "entity_name": str(
            stored_intro.get("entity_name")
            or lead.get("entity_name")
            or lead.get("party_name")
            or ""
        ),
        "summary": str(stored_intro.get("summary") or lead.get("summary") or ""),
    }
    error = str(scan.get("error") or "").strip()
    return {
        "_id": scan.get("_id"),
        "project_id": str(scan.get("project_id") or ""),
        "url": url,
        "endpoint_key": str(scan.get("endpoint_key") or endpoint_identity(url)),
        "task_id": str(scan.get("task_id") or ""),
        "source": str(scan.get("source") or "web_tagging"),
        "target_id": str(scan.get("target_id") or ""),
        "created_at": _created_at(scan),
        "screenshot_object_id": str(scan.get("screenshot_object_id") or ""),
        "screenshot_url": str(scan.get("screenshot_url") or ""),
        "data": {
            "intro": intro,
            "has_findings": bool(ordered_findings),
            "no_findings_reason": (
                error
                or (None if ordered_findings else "扫描完成，未发现符合条件的信息")
            ),
            "findings": ordered_findings,
            "screenshot_object_id": str(scan.get("screenshot_object_id") or ""),
            "screenshot_url": str(scan.get("screenshot_url") or ""),
        },
    }


async def _list_url_scan_candidates(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    candidate_limit: int | None,
) -> tuple[list[dict[str, Any]], int]:
    query: dict[str, Any] = {
        "project_id": project_id,
        "source": "web_tagging",
        "excluded": {"$ne": True},
    }
    if target_id:
        query["target_id"] = target_id
    collection = db[URL_SCAN_RESULTS_COLLECTION]
    if hasattr(collection, "aggregate"):
        sort = {
            "high_risk_count": -1,
            "finding_count": -1,
            "max_attention_score": -1,
            "updated_at": -1,
        }
        group_id = {
            "target_id": "$target_id",
            "endpoint_key": {"$ifNull": ["$endpoint_key", "$url"]},
        }
        rows_pipeline: list[dict[str, Any]] = [
            {"$match": query},
            {"$sort": sort},
            {"$group": {"_id": group_id, "record": {"$first": "$$ROOT"}}},
            {"$replaceRoot": {"newRoot": "$record"}},
            {"$sort": sort},
        ]
        if candidate_limit is not None:
            rows_pipeline.append({"$limit": max(1, candidate_limit)})
        count_pipeline = [
            {"$match": query},
            {"$group": {"_id": group_id}},
            {"$count": "total"},
        ]
        rows, counts = await asyncio.gather(
            collection.aggregate(rows_pipeline).to_list(candidate_limit),
            collection.aggregate(count_pipeline).to_list(1),
        )
        return rows, int(counts[0]["total"] if counts else 0)
    total = await collection.count_documents(query)
    length = candidate_limit if candidate_limit is not None else max(1, total)
    return await collection.find(query).to_list(length), total


async def _list_url_scan_records(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    candidate_limit: int | None,
) -> tuple[list[dict[str, Any]], int]:
    scans, total = await _list_url_scan_candidates(
        db,
        project_id=project_id,
        target_id=target_id,
        candidate_limit=candidate_limit,
    )
    if not scans:
        return [], total
    task_ids = list({str(item.get("task_id") or "") for item in scans})
    findings_by_record: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    cursor = db[FINDINGS_COLLECTION].find(
        {
            "project_id": project_id,
            "source": "web_tagging",
            "task_id": {"$in": task_ids},
        },
        {"_id": 0},
    )
    async for finding in cursor:
        key = (
            str(finding.get("task_id") or ""),
            endpoint_identity(str(finding.get("source_url") or finding.get("url") or "")),
        )
        findings_by_record[key].append(finding)
    records = []
    for scan in scans:
        key = (
            str(scan.get("task_id") or ""),
            str(scan.get("endpoint_key") or endpoint_identity(str(scan.get("url") or ""))),
        )
        record = _adapt_url_scan_record(scan, findings_by_record.get(key, []))
        if not _is_excluded(record):
            records.append(record)
    return records, total


def _deduplicate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_endpoint: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if _is_excluded(record):
            continue
        url = str(record.get("url") or "")
        key = (
            str(record.get("target_id") or ""),
            str(record.get("endpoint_key") or endpoint_identity(url)),
        )
        existing = by_endpoint.get(key)
        if existing is None or _record_sort_key(record) > _record_sort_key(existing):
            if existing:
                record["url"] = prefer_https_url(str(existing.get("url") or ""), url)
            by_endpoint[key] = record
        elif existing is not None:
            existing["url"] = prefer_https_url(
                str(existing.get("url") or ""),
                url,
            )
    return list(by_endpoint.values())


async def _load_unified_records(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str = "",
) -> list[dict[str, Any]]:
    """完整构建兼容读模型，所有过滤、去重与排序均在分页前完成。"""
    selected_target_id = str(target_id or "").strip()
    url_records, legacy_result = await asyncio.gather(
        _list_url_scan_records(
            db,
            project_id=project_id,
            target_id=selected_target_id,
            candidate_limit=None,
        ),
        web_tagging_dao.list_web_tagging_results(
            db,
            project_id=project_id,
            limit=None,
            skip=0,
            source="web_tagging",
            target_id=selected_target_id,
        ),
    )
    records = _deduplicate_records([*url_records[0], *legacy_result[0]])
    records.sort(key=_record_sort_key, reverse=True)
    return records


async def list_website_records(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    skip: int = 0,
    limit: int = 50,
    target_id: str = "",
) -> tuple[list[dict[str, Any]], int]:
    bounded_limit = max(1, min(int(limit or 50), 200))
    bounded_skip = max(0, int(skip or 0))
    records = await _load_unified_records(
        db,
        project_id=project_id,
        target_id=str(target_id or "").strip(),
    )
    return records[bounded_skip : bounded_skip + bounded_limit], len(records)


async def count_project_website_records_by_target(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
) -> dict[str, int]:
    selected = {str(value or "").strip() for value in target_ids if str(value or "").strip()}
    counts = {target_id: 0 for target_id in selected}
    if not selected:
        return counts

    async def _list_scan_identities() -> list[dict[str, Any]]:
        cursor = db[URL_SCAN_RESULTS_COLLECTION].find(
            {
                "project_id": project_id,
                "source": "web_tagging",
                "target_id": {"$in": list(selected)},
                "excluded": {"$ne": True},
            },
            {
                "_id": 0,
                "target_id": 1,
                "url": 1,
                "endpoint_key": 1,
                "excluded": 1,
                "intro": 1,
            },
        )
        return [doc async for doc in cursor]

    scan_records, legacy_records = await asyncio.gather(
        _list_scan_identities(),
        web_tagging_dao.list_web_tagging_identities(
            db,
            project_id=project_id,
            target_ids=list(selected),
        ),
    )
    identities: set[tuple[str, str]] = set()
    for record in [*scan_records, *legacy_records]:
        target_id = str(record.get("target_id") or "").strip()
        if target_id not in counts or _is_excluded(record):
            continue
        url = str(record.get("url") or "")
        endpoint_key = str(record.get("endpoint_key") or endpoint_identity(url))
        identities.add((target_id, endpoint_key))
    for target_id, _endpoint_key in identities:
        counts[target_id] += 1
    return counts
