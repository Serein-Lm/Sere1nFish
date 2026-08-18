"""
统一 Findings DAO

所有数据源（web_tagging、xhs、douyin）的 findings 统一存储和查询。
每个 finding 都有 project_id、task_id、source、finding_id。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from api.db.collections import FINDINGS_COLLECTION, COPYWRITINGS_COLLECTION, PROFILES_COLLECTION
from api.utils.url_identity import endpoint_identity


_FINDING_SUMMARY_PROJECTION = {
    "_id": 0,
    "finding_id": 1,
    "project_id": 1,
    "task_id": 1,
    "task_ids": 1,
    "source": 1,
    "type": 1,
    "channel": 1,
    "label": 1,
    "value": 1,
    "url": 1,
    "attention_score": 1,
    "attention_reason": 1,
    "target_id": 1,
    "target_ids": 1,
    "target_name": 1,
    "has_profile": 1,
    "notes_count": 1,
    "group_key": 1,
    "duplicate_count": 1,
    "evidence_count": 1,
    "finding_ids": 1,
    "source_urls": 1,
    "sources": 1,
    "finding_types": 1,
    "channels": 1,
    "created_at": 1,
    "updated_at": 1,
}

_FINDING_GROUP_IDENTITY_VERSION = 2
_PHONE_CHANNELS = {"phone", "telephone", "tel", "mobile", "hotline"}
_EMAIL_CHANNELS = {"email", "e-mail", "mail"}
_WECHAT_CHANNELS = {"wechat", "weixin", "enterprise_wechat"}
_QQ_CHANNELS = {"qq", "qq_group"}
_CONTACT_FINDING_TYPES = {
    "personal_mobile",
    "personal_email",
    "personal_wechat",
    "enterprise_wechat",
    "hr_contact",
    "business_contact",
    "media_contact",
    "customer_service",
    "group_chat",
    "contact",
}
_MANAGED_UPSERT_FIELDS = {
    "_id",
    "created_at",
    "updated_at",
    "task_ids",
    "evidence_history",
    "source_urls",
    "finding_ids",
    "sources",
    "finding_types",
    "channels",
    "duplicate_count",
    "evidence_count",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def task_finding_scope(task_id: str) -> dict[str, Any]:
    """匹配一个任务及其持久化子任务产生的 Finding。"""
    normalized = str(task_id or "").strip()
    if not normalized:
        return {}
    pattern = re.compile(rf"^{re.escape(normalized)}(?:_|$)")
    return {
        "$or": [
            {"task_id": pattern},
            {"task_ids": pattern},
        ]
    }


# ── Findings CRUD ──

async def insert_finding(db: AsyncIOMotorDatabase, finding: dict[str, Any]) -> None:
    """插入单个 finding"""
    prepared = _prepare_finding_identity(finding)
    prepared.setdefault("created_at", _now())
    prepared.setdefault("updated_at", prepared["created_at"])
    await db[FINDINGS_COLLECTION].insert_one(prepared)


async def insert_findings_batch(db: AsyncIOMotorDatabase, findings: list[dict[str, Any]]) -> int:
    """批量插入 findings"""
    if not findings:
        return 0
    now = _now()
    prepared = []
    for finding in findings:
        item = _prepare_finding_identity(finding)
        item.setdefault("created_at", now)
        item.setdefault("updated_at", item["created_at"])
        prepared.append(item)
    result = await db[FINDINGS_COLLECTION].insert_many(prepared)
    return len(result.inserted_ids)


def stable_finding_id(finding: dict[str, Any]) -> str:
    """Build a stable identity for one project/source/evidence contact."""
    parts = [
        str(finding.get("project_id") or ""),
        str(finding.get("target_id") or ""),
        str(finding.get("source") or ""),
        str(finding.get("bidding_record_id") or ""),
        str(finding.get("source_document_id") or ""),
        endpoint_identity(
            str(finding.get("url") or finding.get("source_url") or "")
        ),
        str(finding.get("channel") or ""),
        str(finding.get("value") or "").strip().casefold(),
        str(finding.get("party_name") or "").strip().casefold(),
        str(finding.get("type") or ""),
    ]
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return "fnd_" + digest


def _normalized_target_identity(finding: dict[str, Any]) -> str:
    target_ids = _finding_target_ids(finding)
    if target_ids:
        return sorted(target_ids)[0]
    for field in ("target_name", "entity_name"):
        value = re.sub(
            r"[^0-9a-z\u4e00-\u9fff]+",
            "",
            str(finding.get(field) or "").strip().casefold(),
        )
        if value:
            return f"name:{value}"
    return ""


def _canonical_finding_value(finding: dict[str, Any]) -> tuple[str, str]:
    """Return a channel/value identity for cross-document deduplication."""
    raw_value = str(finding.get("value") or "").strip()
    if not raw_value:
        return "", ""

    raw_channel = str(finding.get("channel") or "").strip().casefold()
    finding_type = str(finding.get("type") or "").strip().casefold()
    lowered = raw_value.casefold()

    url_value = raw_value
    if lowered.startswith("www."):
        url_value = f"https://{raw_value}"
    if lowered.startswith(("http://", "https://", "www.")):
        normalized_url = endpoint_identity(url_value)
        if normalized_url:
            return "link", normalized_url

    if raw_channel in _PHONE_CHANNELS:
        digits = re.sub(r"\D", "", raw_value.removeprefix("tel:"))
        if digits.startswith("0086") and len(digits) > 11:
            digits = digits[4:]
        elif digits.startswith("86") and 12 <= len(digits) <= 14:
            digits = digits[2:]
        return ("phone", digits) if digits else ("", "")

    if raw_channel in _EMAIL_CHANNELS:
        normalized_email = lowered.removeprefix("mailto:").strip()
        return ("email", normalized_email) if normalized_email else ("", "")

    if raw_channel in _WECHAT_CHANNELS:
        normalized_wechat = re.sub(r"\s+", "", lowered)
        return ("wechat", normalized_wechat) if normalized_wechat else ("", "")

    if raw_channel in _QQ_CHANNELS:
        normalized_qq = re.sub(r"\s+", "", lowered)
        return ("qq", normalized_qq) if normalized_qq else ("", "")

    if finding_type in _CONTACT_FINDING_TYPES and raw_channel not in {
        "link",
        "form",
    }:
        normalized_value = re.sub(r"\s+", " ", lowered).strip()
        return (raw_channel or "contact", normalized_value)

    return "", ""


def finding_group_key(finding: dict[str, Any]) -> str:
    """Build the logical identity shared by the same Finding across websites.

    Raw findings remain source-specific for auditability. This key powers a
    grouped read model without erasing source documents or historical IDs.
    """
    project_id = str(finding.get("project_id") or "").strip()
    target_identity = _normalized_target_identity(finding)
    channel, value = _canonical_finding_value(finding)
    if not project_id or not target_identity or not channel or not value:
        return ""
    raw = "\x1f".join(
        (
            f"v{_FINDING_GROUP_IDENTITY_VERSION}",
            project_id,
            target_identity,
            channel,
            value,
        )
    )
    return "fgrp_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _finding_source_urls(finding: dict[str, Any]) -> list[str]:
    values = finding.get("source_urls") or []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = []
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in [
                *values,
                finding.get("source_url"),
                finding.get("url"),
            ]
            if str(value or "").strip().lower().startswith(("http://", "https://"))
        )
    )


def _prepare_finding_identity(finding: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(finding)
    finding_id = str(prepared.get("finding_id") or stable_finding_id(prepared))
    channel, value = _canonical_finding_value(prepared)
    group_key = finding_group_key(prepared)
    prepared.update(
        {
            "finding_id": finding_id,
            "group_key": group_key or finding_id,
            "groupable": bool(group_key),
            "group_identity_version": _FINDING_GROUP_IDENTITY_VERSION,
        }
    )
    if group_key:
        prepared["normalized_channel"] = channel
        prepared["normalized_value"] = value
    else:
        prepared.pop("normalized_channel", None)
        prepared.pop("normalized_value", None)
    source_urls = _finding_source_urls(prepared)
    if source_urls:
        prepared["source_urls"] = source_urls
    return prepared


def _finding_target_ids(finding: dict[str, Any]) -> list[str]:
    raw_target_ids = finding.get("target_ids") or []
    if isinstance(raw_target_ids, str):
        raw_target_ids = [raw_target_ids]
    elif not isinstance(raw_target_ids, (list, tuple, set)):
        raw_target_ids = []
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in [
                finding.get("target_id"),
                *raw_target_ids,
            ]
            if str(value or "").strip()
        )
    )


def _prepare_finding_upsert(
    raw: dict[str, Any],
    *,
    now: datetime,
) -> tuple[str, dict[str, Any], dict[str, Any], datetime]:
    prepared = _prepare_finding_identity(raw)
    finding_id = str(prepared["finding_id"])
    source_urls = _finding_source_urls(prepared)
    set_fields = {
        key: value
        for key, value in prepared.items()
        if key not in _MANAGED_UPSERT_FIELDS
    }
    set_fields["updated_at"] = now

    additions: dict[str, Any] = {}
    task_id = str(prepared.get("task_id") or "").strip()
    evidence = str(prepared.get("evidence") or "").strip()
    if task_id:
        additions["task_ids"] = task_id
    if evidence:
        additions["evidence_history"] = evidence[:4_000]
    if source_urls:
        additions["source_urls"] = {"$each": source_urls}

    created_at = raw.get("created_at")
    if not isinstance(created_at, datetime):
        created_at = now
    return finding_id, set_fields, additions, created_at


def _mongo_finding_group_key() -> dict[str, Any]:
    return {
        "$cond": [
            {
                "$and": [
                    {"$ne": [{"$ifNull": ["$group_key", ""]}, ""]},
                    {"$ne": ["$group_key", None]},
                ]
            },
            "$group_key",
            {"$ifNull": ["$finding_id", "$_id"]},
        ]
    }


def _grouped_finding_read_stages() -> list[dict[str, Any]]:
    """Build the shared Mongo read model for one logical Finding group."""
    return [
        {
            "$set": {
                "_logical_group_key": _mongo_finding_group_key(),
                "_row_source_urls": {
                    "$setUnion": [
                        {
                            "$cond": [
                                {"$isArray": "$source_urls"},
                                "$source_urls",
                                [],
                            ]
                        },
                        [{"$ifNull": ["$source_url", ""]}],
                        [{"$ifNull": ["$url", ""]}],
                    ]
                },
                "_row_evidence_count": {
                    "$let": {
                        "vars": {
                            "count": {
                                "$size": {
                                    "$cond": [
                                        {"$isArray": "$evidence_refs"},
                                        "$evidence_refs",
                                        [],
                                    ]
                                }
                            }
                        },
                        "in": {
                            "$cond": [
                                {"$gt": ["$$count", 0]},
                                "$$count",
                                1,
                            ]
                        },
                    }
                },
            }
        },
        {
            "$sort": {
                "attention_score": -1,
                "updated_at": -1,
                "created_at": -1,
                "finding_id": 1,
            }
        },
        {
            "$group": {
                "_id": "$_logical_group_key",
                "representative": {"$first": "$$ROOT"},
                "finding_ids": {"$addToSet": "$finding_id"},
                "source_url_lists": {"$push": "$_row_source_urls"},
                "sources": {"$addToSet": "$source"},
                "finding_types": {"$addToSet": "$type"},
                "channels": {"$addToSet": "$channel"},
                "duplicate_count": {"$sum": 1},
                "evidence_count": {"$sum": "$_row_evidence_count"},
                "attention_score": {
                    "$max": {"$ifNull": ["$attention_score", 0]}
                },
            }
        },
        {
            "$set": {
                "source_urls": {
                    "$reduce": {
                        "input": "$source_url_lists",
                        "initialValue": [],
                        "in": {"$setUnion": ["$$value", "$$this"]},
                    }
                }
            }
        },
        {
            "$set": {
                "representative.finding_ids": {
                    "$filter": {
                        "input": "$finding_ids",
                        "as": "value",
                        "cond": {
                            "$and": [
                                {"$ne": ["$$value", ""]},
                                {"$ne": ["$$value", None]},
                            ]
                        },
                    }
                },
                "representative.source_urls": {
                    "$filter": {
                        "input": "$source_urls",
                        "as": "value",
                        "cond": {
                            "$and": [
                                {"$ne": ["$$value", ""]},
                                {"$ne": ["$$value", None]},
                            ]
                        },
                    }
                },
                "representative.sources": {
                    "$filter": {
                        "input": "$sources",
                        "as": "value",
                        "cond": {
                            "$and": [
                                {"$ne": ["$$value", ""]},
                                {"$ne": ["$$value", None]},
                            ]
                        },
                    }
                },
                "representative.finding_types": {
                    "$filter": {
                        "input": "$finding_types",
                        "as": "value",
                        "cond": {
                            "$and": [
                                {"$ne": ["$$value", ""]},
                                {"$ne": ["$$value", None]},
                            ]
                        },
                    }
                },
                "representative.channels": {
                    "$filter": {
                        "input": "$channels",
                        "as": "value",
                        "cond": {
                            "$and": [
                                {"$ne": ["$$value", ""]},
                                {"$ne": ["$$value", None]},
                            ]
                        },
                    }
                },
                "representative.duplicate_count": "$duplicate_count",
                "representative.evidence_count": "$evidence_count",
                "representative.attention_score": "$attention_score",
            }
        },
        {"$replaceRoot": {"newRoot": "$representative"}},
        {
            "$unset": [
                "_logical_group_key",
                "_row_source_urls",
                "_row_evidence_count",
            ]
        },
    ]


async def _query_grouped_findings(
    db: AsyncIOMotorDatabase,
    *,
    query: dict[str, Any],
    sort_spec: list[tuple[str, int]],
    limit: int,
    skip: int = 0,
    projection: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    item_pipeline: list[dict[str, Any]] = [
        {"$sort": dict(sort_spec)},
        {"$skip": max(0, int(skip or 0))},
        {"$limit": max(1, int(limit or 1))},
    ]
    if projection is not None:
        item_pipeline.append({"$project": projection})
    pipeline = [
        {"$match": query},
        *_grouped_finding_read_stages(),
        {
            "$facet": {
                "items": item_pipeline,
                "total": [{"$count": "count"}],
            }
        },
    ]
    rows = await db[FINDINGS_COLLECTION].aggregate(pipeline).to_list(1)
    if not rows:
        return [], 0
    result = rows[0]
    totals = result.get("total") or []
    total = int(totals[0].get("count") or 0) if totals else 0
    return list(result.get("items") or []), total


async def enrich_with_target_relations(
    db: AsyncIOMotorDatabase,
    findings: list[dict[str, Any]],
    *,
    project_id: str = "",
) -> list[dict[str, Any]]:
    """Attach current ProjectTarget ownership without duplicating it in Finding."""
    from api.dao import targets as targets_dao

    if not findings:
        return []
    target_ids_by_project: dict[str, list[str]] = {}
    for finding in findings:
        resolved_project_id = str(project_id or finding.get("project_id") or "")
        if not resolved_project_id:
            continue
        target_ids_by_project.setdefault(resolved_project_id, []).extend(
            _finding_target_ids(finding)
        )
    project_ids = list(target_ids_by_project)
    loaded_views = await asyncio.gather(
        *(
            targets_dao.get_project_target_relation_views(
                db,
                project_id=resolved_project_id,
                target_ids=list(
                    dict.fromkeys(target_ids_by_project[resolved_project_id])
                ),
            )
            for resolved_project_id in project_ids
        )
    )
    views_by_project = dict(zip(project_ids, loaded_views, strict=True))
    enriched: list[dict[str, Any]] = []
    for finding in findings:
        item = dict(finding)
        resolved_project_id = str(project_id or finding.get("project_id") or "")
        relation_views = views_by_project.get(resolved_project_id, {})
        relations = [
            relation_views[target_id]
            for target_id in _finding_target_ids(finding)
            if target_id in relation_views
        ]
        if relations:
            item["target_relation"] = relations[0]
            item["target_relations"] = relations
            item.setdefault("target_name", relations[0].get("target_name") or "")
        enriched.append(item)
    return enriched


async def upsert_findings_batch(
    db: AsyncIOMotorDatabase,
    findings: list[dict[str, Any]],
) -> int:
    """Idempotently refresh findings while retaining task and evidence history."""
    if not findings:
        return 0
    now = _now()
    operations: list[UpdateOne] = []
    for raw in findings:
        finding_id, set_fields, add_to_set, created_at = _prepare_finding_upsert(
            raw,
            now=now,
        )
        update: dict[str, Any] = {
            "$set": set_fields,
            "$setOnInsert": {"created_at": created_at},
        }
        if add_to_set:
            update["$addToSet"] = add_to_set
        operations.append(UpdateOne({"finding_id": finding_id}, update, upsert=True))
    collection = db[FINDINGS_COLLECTION]
    if not hasattr(collection, "bulk_write"):
        return await insert_findings_batch(db, findings)
    result = await collection.bulk_write(operations, ordered=False)
    return int(result.upserted_count + result.modified_count)


async def query_findings(
    db: AsyncIOMotorDatabase,
    project_id: str,
    source: str = "",
    task_id: str = "",
    target_id: str = "",
    finding_type: str = "",
    min_score: int = 0,
    sort: str = "score_desc",
    limit: int = 20,
    skip: int = 0,
    summary_only: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """
    分页查询 findings，返回 (findings, total)
    """
    query: dict[str, Any] = {"project_id": project_id}
    if source:
        query["source"] = source
    if task_id:
        query.update(task_finding_scope(task_id))
    if target_id:
        query["target_id"] = target_id
    if finding_type:
        query["type"] = finding_type
    if min_score > 0:
        query["attention_score"] = {"$gte": min_score}

    sort_map = {
        "score_desc": [("attention_score", -1)],
        "score_asc": [("attention_score", 1)],
        "time_desc": [("created_at", -1)],
    }
    sort_spec = sort_map.get(sort, [("attention_score", -1)])

    projection = _FINDING_SUMMARY_PROJECTION if summary_only else {"_id": 0}
    findings, total = await _query_grouped_findings(
        db,
        query=query,
        sort_spec=sort_spec,
        limit=limit,
        skip=skip,
        projection=projection,
    )

    return await enrich_with_target_relations(
        db,
        findings,
        project_id=project_id,
    ), total


async def get_finding(db: AsyncIOMotorDatabase, finding_id: str) -> dict[str, Any] | None:
    """获取单个 finding"""
    finding = await db[FINDINGS_COLLECTION].find_one(
        {"finding_id": finding_id},
        {"_id": 0},
    )
    if not finding:
        return None
    if finding.get("groupable") and finding.get("group_key"):
        grouped = await db[FINDINGS_COLLECTION].find(
            {
                "project_id": finding.get("project_id"),
                "group_key": finding.get("group_key"),
            },
            {
                "_id": 0,
                "finding_id": 1,
                "source": 1,
                "type": 1,
                "channel": 1,
                "url": 1,
                "source_url": 1,
                "source_urls": 1,
                "evidence_refs": 1,
            },
        ).to_list(500)
        source_urls = list(
            dict.fromkeys(
                url
                for item in grouped
                for url in _finding_source_urls(item)
            )
        )
        finding["finding_ids"] = [
            str(item.get("finding_id") or "")
            for item in grouped
            if str(item.get("finding_id") or "")
        ]
        finding["source_urls"] = source_urls
        finding["sources"] = sorted(
            {
                str(item.get("source") or "")
                for item in grouped
                if str(item.get("source") or "")
            }
        )
        finding["finding_types"] = sorted(
            {
                str(item.get("type") or "")
                for item in grouped
                if str(item.get("type") or "")
            }
        )
        finding["channels"] = sorted(
            {
                str(item.get("channel") or "")
                for item in grouped
                if str(item.get("channel") or "")
            }
        )
        finding["duplicate_count"] = len(grouped)
        finding["evidence_count"] = sum(
            max(1, len(item.get("evidence_refs") or [])) for item in grouped
        )
    enriched = await enrich_with_target_relations(db, [finding])
    return enriched[0]


async def query_target_findings_with_copywriting(
    db: AsyncIOMotorDatabase,
    target_id: str,
    *,
    project_id: str = "",
    min_score: int = 0,
    limit: int = 20,
    skip: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Query one Target's findings and join existing copywriting by finding id."""
    target_id = str(target_id or "").strip()
    if not target_id:
        return [], 0

    query: dict[str, Any] = {
        "$or": [
            {"target_id": target_id},
            {"target_ids": target_id},
        ]
    }
    if project_id:
        query["project_id"] = str(project_id).strip()
    if min_score > 0:
        query["attention_score"] = {"$gte": min_score}

    bounded_limit = max(1, min(int(limit or 20), 50))
    bounded_skip = max(0, min(int(skip or 0), 10_000))
    findings, total = await _query_grouped_findings(
        db,
        query=query,
        sort_spec=[("attention_score", -1), ("created_at", -1)],
        skip=bounded_skip,
        limit=bounded_limit,
        projection={"_id": 0},
    )

    findings = await enrich_with_target_relations(
        db,
        findings,
        project_id=project_id,
    )
    finding_ids = list(
        dict.fromkeys(
            str(finding_id or "")
            for item in findings
            for finding_id in (
                item.get("finding_ids")
                or [item.get("finding_id")]
            )
            if str(finding_id or "")
        )
    )
    copywriting_by_finding: dict[str, dict[str, Any]] = {}
    if finding_ids:
        cursor = (
            db[COPYWRITINGS_COLLECTION]
            .find({"finding_id": {"$in": finding_ids}}, {"_id": 0})
            .sort("created_at", -1)
        )
        async for item in cursor:
            finding_id = str(item.get("finding_id") or "")
            if finding_id and finding_id not in copywriting_by_finding:
                copywriting_by_finding[finding_id] = item

    grouped_results = []
    for item in findings:
        member_ids = item.get("finding_ids") or [item.get("finding_id")]
        copywriting = next(
            (
                copywriting_by_finding[str(member_id)]
                for member_id in member_ids
                if str(member_id) in copywriting_by_finding
            ),
            None,
        )
        grouped_results.append({**item, "copywriting": copywriting})
    return grouped_results, total


async def query_target_dashboard_findings(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_id: str,
    top_limit: int = 12,
    contact_limit: int = 500,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load the bounded Finding projections needed by one Target dashboard."""
    project_id = str(project_id or "").strip()
    target_id = str(target_id or "").strip()
    if not project_id or not target_id:
        return [], []

    target_query: dict[str, Any] = {
        "project_id": project_id,
        "$or": [
            {"target_id": target_id},
            {"target_ids": target_id},
        ],
    }
    contact_query: dict[str, Any] = {
        "$and": [
            target_query,
            {"channel": {"$in": ["phone", "telephone", "email"]}},
            {"value": {"$nin": [None, ""]}},
            {
                "$or": [
                    # Generic collectors already distinguish mobile numbers as
                    # ``phone`` and landlines as ``telephone``.  The service
                    # performs a second format check before exposing a number.
                    {"channel": "phone"},
                    {"type": {"$in": ["personal_mobile", "personal_email"]}},
                    {"scope": "personal"},
                    {
                        "subtype": {
                            "$in": ["mobile_personal", "email_personal"]
                        }
                    },
                ]
            },
        ]
    }
    projection = {
        "_id": 0,
        "finding_id": 1,
        "source": 1,
        "type": 1,
        "scope": 1,
        "channel": 1,
        "subtype": 1,
        "role": 1,
        "label": 1,
        "value": 1,
        "context": 1,
        "summary": 1,
        "attention_score": 1,
        "party_name": 1,
        "party_role": 1,
        "entity_name": 1,
        "target_relation": 1,
        "url": 1,
        "source_url": 1,
        "source_document_id": 1,
        "screenshot_url": 1,
        "evidence_refs": 1,
        "latest_evidence_ref": 1,
        "group_key": 1,
        "duplicate_count": 1,
        "evidence_count": 1,
        "finding_ids": 1,
        "source_urls": 1,
        "sources": 1,
        "finding_types": 1,
        "channels": 1,
        "created_at": 1,
        "updated_at": 1,
    }
    bounded_top_limit = max(1, min(int(top_limit or 12), 30))
    bounded_contact_limit = max(20, min(int(contact_limit or 500), 1_000))
    top_job = _query_grouped_findings(
        db,
        query={**target_query, "attention_score": {"$gte": 70}},
        projection=projection,
        sort_spec=[
            ("attention_score", -1),
            ("updated_at", -1),
            ("created_at", -1),
        ],
        limit=bounded_top_limit,
    )
    contacts_job = _query_grouped_findings(
        db,
        query=contact_query,
        projection=projection,
        sort_spec=[
            ("attention_score", -1),
            ("updated_at", -1),
            ("created_at", -1),
        ],
        limit=bounded_contact_limit,
    )
    (top_findings, _), (contact_findings, _) = await asyncio.gather(
        top_job,
        contacts_job,
    )
    return list(top_findings), list(contact_findings)


def mobile_profile_finding_id(project_id: str, contact_id: str) -> str:
    """Stable finding id for one mobile contact profile within a project."""
    raw = f"mobile:{project_id}:{contact_id}".encode("utf-8")
    return "mp_" + hashlib.sha1(raw).hexdigest()[:20]


def _mobile_profile_score(persona: dict[str, Any] | None) -> int:
    persona = persona or {}
    score = 35
    for key in ("summary", "background", "personality", "communication_style"):
        if persona.get(key):
            score += 8
    score += min(len(persona.get("interests") or []) * 3, 12)
    score += min(len(persona.get("tags") or []) * 4, 16)
    if persona.get("common_phrases"):
        score += 6
    if persona.get("risk_signals"):
        score += 10
    return max(0, min(score, 100))


async def upsert_mobile_profile_finding(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    contact_id: str,
    task_id: str | None = None,
    device_id: str | None = None,
    platform: str | None = None,
    name: str | None = None,
    persona: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    attention_score: int | None = None,
) -> dict[str, Any]:
    """Create/update the unified finding for a mobile contact profile.

    A project/contact pair maps to one deterministic finding id. The finding is
    the queryable project-level index; the current rich profile snapshot is
    stored in ``profiles`` and granular evidence lives in mobile observations.
    """
    finding_id = mobile_profile_finding_id(project_id, contact_id)
    existing = await get_finding(db, finding_id)
    persona = persona or {}
    display_name = name or persona.get("name") or contact_id
    score = attention_score if attention_score is not None else _mobile_profile_score(persona)
    summary = (
        persona.get("summary")
        or persona.get("communication_style")
        or persona.get("background")
        or ""
    )
    now = _now()
    set_fields = {
        "finding_id": finding_id,
        "project_id": project_id,
        "task_id": task_id or (existing or {}).get("task_id", ""),
        "latest_task_id": task_id,
        "source": "mobile",
        "type": "contact_profile",
        "channel": "mobile_chat_profile",
        "label": f"手机聊天画像: {display_name}",
        "value": display_name,
        "contact_id": contact_id,
        "device_id": device_id,
        "platform": platform,
        "has_profile": True,
        "attention_score": score,
        "attention_reason": summary,
        "context": f"手机聊天联系人 {display_name} 的画像沉淀",
        "evidence": evidence or {},
        "updated_at": now,
    }
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now},
    }
    if task_id:
        update["$addToSet"] = {"task_ids": task_id}
    await db[FINDINGS_COLLECTION].update_one(
        {"finding_id": finding_id},
        update,
        upsert=True,
    )
    return await get_finding(db, finding_id) or set_fields


async def upsert_contact_finding(
    db: AsyncIOMotorDatabase, finding: dict[str, Any]
) -> dict[str, Any]:
    """按 finding_id 幂等 upsert 一个联系方式类 finding。

    finding 需已含确定性 finding_id(同一项目同一联系方式映射同一条),
    避免多次采集重复插入。created_at 仅首次写入,task_ids 累积。
    """
    prepared = _prepare_finding_identity(finding)
    finding_id = prepared["finding_id"]
    now = _now()
    task_id = prepared.get("task_id")
    set_fields = {
        key: value
        for key, value in prepared.items()
        if key
        not in (
            _MANAGED_UPSERT_FIELDS
            | {"evidence_refs", "source_document_ids", "target_ids"}
        )
    }
    evidence_ref = set_fields.pop("evidence_ref", None)
    if evidence_ref:
        set_fields["latest_evidence_ref"] = evidence_ref
    set_fields["updated_at"] = now
    update: dict[str, Any] = {
        "$set": set_fields,
        "$setOnInsert": {"created_at": now},
    }
    additions: dict[str, Any] = {}
    if task_id:
        additions["task_ids"] = task_id
    if evidence_ref:
        additions["evidence_refs"] = evidence_ref
    if finding.get("source_document_id"):
        additions["source_document_ids"] = finding["source_document_id"]
    if finding.get("target_id"):
        additions["target_ids"] = finding["target_id"]
    source_urls = _finding_source_urls(prepared)
    if source_urls:
        additions["source_urls"] = {"$each": source_urls}
    if additions:
        update["$addToSet"] = additions
    await db[FINDINGS_COLLECTION].update_one(
        {"finding_id": finding_id}, update, upsert=True
    )
    return await get_finding(db, finding_id) or set_fields


async def upsert_contact_findings_batch(
    db: AsyncIOMotorDatabase,
    findings: list[dict[str, Any]],
) -> int:
    """Bulk-upsert contact findings while preserving evidence history."""
    if not findings:
        return 0
    now = _now()
    operations: list[UpdateOne] = []
    seen: set[str] = set()
    for raw in findings:
        finding = _prepare_finding_identity(raw)
        finding_id = str(finding.get("finding_id") or "")
        if not finding_id or finding_id in seen:
            continue
        seen.add(finding_id)
        task_id = str(finding.get("task_id") or "")
        set_fields = {
            key: value
            for key, value in finding.items()
            if key
            not in (
                _MANAGED_UPSERT_FIELDS
                | {"evidence_refs", "source_document_ids", "target_ids"}
            )
        }
        evidence_ref = set_fields.pop("evidence_ref", None)
        if evidence_ref:
            set_fields["latest_evidence_ref"] = evidence_ref
        set_fields["updated_at"] = now
        update: dict[str, Any] = {
            "$set": set_fields,
            "$setOnInsert": {"created_at": now},
        }
        additions: dict[str, Any] = {}
        if task_id:
            additions["task_ids"] = task_id
        if evidence_ref:
            additions["evidence_refs"] = evidence_ref
        if finding.get("source_document_id"):
            additions["source_document_ids"] = finding["source_document_id"]
        if finding.get("target_id"):
            additions["target_ids"] = finding["target_id"]
        source_urls = _finding_source_urls(finding)
        if source_urls:
            additions["source_urls"] = {"$each": source_urls}
        if additions:
            update["$addToSet"] = additions
        operations.append(
            UpdateOne({"finding_id": finding_id}, update, upsert=True)
        )
    if not operations:
        return 0
    collection = db[FINDINGS_COLLECTION]
    if not hasattr(collection, "bulk_write"):
        for finding in findings:
            await upsert_contact_finding(db, finding)
        return len(operations)
    await collection.bulk_write(operations, ordered=False)
    return len(operations)


async def reconcile_contact_findings_for_record(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    record_id: str,
    keep_finding_ids: list[str],
) -> dict[str, int]:
    """Withdraw stale contact evidence after one record is re-analysed.

    A Finding may aggregate evidence from several records. Only this record's
    evidence is removed; the Finding itself is deleted when no evidence remains.
    """
    if not project_id or not record_id:
        return {"evidence_removed": 0, "findings_deleted": 0}
    keep = {str(value) for value in keep_finding_ids if str(value)}
    collection = db[FINDINGS_COLLECTION]
    cursor = collection.find(
        {
            "project_id": project_id,
            "type": "contact",
            "$or": [
                {"evidence_refs.record_id": record_id},
                {"latest_evidence_ref.record_id": record_id},
                {"evidence.record_id": record_id},
            ],
        },
        {
            "_id": 0,
            "finding_id": 1,
            "evidence_refs": 1,
            "latest_evidence_ref": 1,
            "evidence": 1,
        },
    )
    findings = await cursor.to_list(length=None)
    removed = 0
    deleted = 0
    for finding in findings:
        finding_id = str(finding.get("finding_id") or "")
        if not finding_id or finding_id in keep:
            continue
        result = await collection.update_one(
            {"finding_id": finding_id},
            {"$pull": {"evidence_refs": {"record_id": record_id}}},
        )
        removed += int(result.modified_count or 0)
        current = await collection.find_one(
            {"finding_id": finding_id},
            {
                "_id": 0,
                "evidence_refs": 1,
                "latest_evidence_ref": 1,
                "evidence": 1,
            },
        )
        if not current:
            continue
        remaining = [dict(item) for item in current.get("evidence_refs") or []]
        if remaining:
            latest = remaining[-1]
            if str((current.get("latest_evidence_ref") or {}).get("record_id") or "") == record_id:
                set_fields: dict[str, Any] = {
                    "latest_evidence_ref": latest,
                    "updated_at": _now(),
                }
                for source_key, target_key in (
                    ("source_document_id", "source_document_id"),
                    ("source_document_version_id", "source_document_version_id"),
                    ("source_url", "url"),
                    ("context", "context"),
                ):
                    value = latest.get(source_key)
                    if value:
                        set_fields[target_key] = value
                await collection.update_one(
                    {
                        "finding_id": finding_id,
                        "latest_evidence_ref.record_id": record_id,
                    },
                    {"$set": set_fields},
                )
            continue
        latest_record_id = str(
            (current.get("latest_evidence_ref") or {}).get("record_id") or ""
        )
        legacy_record_id = str(
            (current.get("evidence") or {}).get("record_id") or ""
        )
        if (
            latest_record_id not in {"", record_id}
            or legacy_record_id not in {"", record_id}
        ):
            continue
        result = await collection.delete_one(
            {
                "finding_id": finding_id,
                "$or": [
                    {"evidence_refs": {"$exists": False}},
                    {"evidence_refs": {"$size": 0}},
                ],
            }
        )
        deleted += int(result.deleted_count or 0)
    return {"evidence_removed": removed, "findings_deleted": deleted}


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Ensure indexes for raw provenance and the grouped Finding read model."""
    collection = db[FINDINGS_COLLECTION]
    try:
        await collection.create_index("finding_id", sparse=True)
    except Exception:
        pass
    await collection.create_index("project_id")
    await collection.create_index([("project_id", 1), ("source", 1)])
    await collection.create_index([("project_id", 1), ("attention_score", -1)])
    await collection.create_index("xhs_user_id", sparse=True)
    await collection.create_index("note_id", sparse=True)
    await collection.create_index("task_id")
    await collection.create_index("task_ids", sparse=True)
    await collection.create_index(
        [("project_id", 1), ("task_id", 1), ("attention_score", -1)]
    )
    await collection.create_index(
        [("project_id", 1), ("task_ids", 1), ("attention_score", -1)],
        sparse=True,
    )
    await collection.create_index("target_id", sparse=True)
    await collection.create_index(
        [("target_id", 1), ("attention_score", -1)],
        sparse=True,
    )
    await collection.create_index("target_ids", sparse=True)
    await collection.create_index("source_document_id", sparse=True)
    await collection.create_index(
        [("project_id", 1), ("source", 1), ("bidding_record_id", 1)],
        sparse=True,
    )
    await collection.create_index(
        [("project_id", 1), ("group_key", 1), ("attention_score", -1)],
        sparse=True,
    )
    await collection.create_index(
        [("project_id", 1), ("target_id", 1), ("group_key", 1)],
        sparse=True,
    )


async def backfill_finding_group_keys(
    db: AsyncIOMotorDatabase,
    *,
    batch_size: int = 500,
) -> dict[str, int]:
    """Idempotently add logical identities to findings created before v1."""
    collection = db[FINDINGS_COLLECTION]
    bounded_batch_size = max(50, min(int(batch_size or 500), 2_000))
    scanned = 0
    modified = 0
    groupable = 0
    query = {"group_identity_version": {"$ne": _FINDING_GROUP_IDENTITY_VERSION}}
    projection = {
        "project_id": 1,
        "target_id": 1,
        "target_ids": 1,
        "target_name": 1,
        "entity_name": 1,
        "source": 1,
        "bidding_record_id": 1,
        "source_document_id": 1,
        "url": 1,
        "source_url": 1,
        "source_urls": 1,
        "channel": 1,
        "value": 1,
        "party_name": 1,
        "type": 1,
        "finding_id": 1,
    }
    while True:
        documents = await collection.find(query, projection).limit(
            bounded_batch_size
        ).to_list(bounded_batch_size)
        if not documents:
            break
        operations: list[UpdateOne] = []
        for document in documents:
            prepared = _prepare_finding_identity(document)
            identity_fields: dict[str, Any] = {
                "finding_id": prepared["finding_id"],
                "group_key": prepared["group_key"],
                "groupable": prepared["groupable"],
                "group_identity_version": prepared["group_identity_version"],
            }
            if prepared.get("normalized_channel"):
                identity_fields["normalized_channel"] = prepared[
                    "normalized_channel"
                ]
                identity_fields["normalized_value"] = prepared["normalized_value"]
            update: dict[str, Any] = {"$set": identity_fields}
            if not prepared.get("normalized_channel"):
                update["$unset"] = {
                    "normalized_channel": "",
                    "normalized_value": "",
                }
            source_urls = _finding_source_urls(prepared)
            if source_urls:
                update["$addToSet"] = {
                    "source_urls": {"$each": source_urls}
                }
            operations.append(
                UpdateOne({"_id": document["_id"]}, update, upsert=False)
            )
            groupable += int(bool(prepared["groupable"]))
        result = await collection.bulk_write(operations, ordered=False)
        scanned += len(documents)
        modified += int(result.modified_count or 0)
    return {"scanned": scanned, "modified": modified, "groupable": groupable}


def _logical_finding_fact_stages() -> list[dict[str, Any]]:
    """Collapse raw source records to one highest-value logical fact."""
    return [
        {"$set": {"_logical_group_key": _mongo_finding_group_key()}},
        {
            "$sort": {
                "attention_score": -1,
                "updated_at": -1,
                "created_at": -1,
                "finding_id": 1,
            }
        },
        {
            "$group": {
                "_id": "$_logical_group_key",
                "representative": {"$first": "$$ROOT"},
                "attention_score": {
                    "$max": {"$ifNull": ["$attention_score", 0]}
                },
            }
        },
        {
            "$set": {
                "representative.attention_score": "$attention_score",
            }
        },
        {"$replaceRoot": {"newRoot": "$representative"}},
        {"$unset": "_logical_group_key"},
    ]


def _logical_finding_top_group_stage(
    representative_output: dict[str, Any],
) -> dict[str, Any]:
    """Collapse logical facts without sorting or retaining full documents."""
    return {
        "$group": {
            "_id": _mongo_finding_group_key(),
            "representative": {
                "$top": {
                    "sortBy": {
                        "attention_score": -1,
                        "updated_at": -1,
                        "created_at": -1,
                        "finding_id": 1,
                    },
                    "output": representative_output,
                }
            },
            "attention_score": {
                "$max": {"$ifNull": ["$attention_score", 0]}
            },
        }
    }


async def aggregate_target_finding_counts(
    db: AsyncIOMotorDatabase,
    *,
    project_id: str,
    target_ids: list[str],
) -> list[dict[str, Any]]:
    """Count logical Findings per Target and representative source."""
    normalized_target_ids = list(
        dict.fromkeys(str(value or "").strip() for value in target_ids if value)
    )
    if not project_id or not normalized_target_ids:
        return []
    pipeline = [
        {
            "$match": {
                "project_id": project_id,
                "target_id": {"$in": normalized_target_ids},
            }
        },
        _logical_finding_top_group_stage(
            {
                "target_id": "$target_id",
                "source": {"$ifNull": ["$source", ""]},
            }
        ),
        {
            "$group": {
                "_id": {
                    "target_id": "$representative.target_id",
                    "source": "$representative.source",
                },
                "finding_count": {"$sum": 1},
                "high_score_count": {
                    "$sum": {
                        "$cond": [
                            {"$gte": [{"$ifNull": ["$attention_score", 0]}, 70]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
    ]
    return await db[FINDINGS_COLLECTION].aggregate(pipeline).to_list(None)


async def get_findings_summary(db: AsyncIOMotorDatabase, project_id: str) -> dict[str, Any]:
    """项目 findings 总览统计"""
    pipeline = [
        {"$match": {"project_id": project_id}},
        _logical_finding_top_group_stage(
            {
                "source": {"$ifNull": ["$source", ""]},
                "type": {"$ifNull": ["$type", ""]},
            }
        ),
        {
            "$project": {
                "source": "$representative.source",
                "type": "$representative.type",
                "attention_score": 1,
            }
        },
        {"$facet": {
            "total": [{"$count": "count"}],
            "by_source": [{"$group": {"_id": "$source", "count": {"$sum": 1}}}],
            "by_type": [{"$group": {"_id": "$type", "count": {"$sum": 1}}}],
            "score_high": [{"$match": {"attention_score": {"$gte": 70}}}, {"$count": "count"}],
            "score_medium": [{"$match": {"attention_score": {"$gte": 40, "$lt": 70}}}, {"$count": "count"}],
            "score_low": [{"$match": {"attention_score": {"$lt": 40}}}, {"$count": "count"}],
        }},
    ]
    result = await db[FINDINGS_COLLECTION].aggregate(pipeline).to_list(1)
    if not result:
        return {"total": 0, "by_source": {}, "by_type": {}, "score_distribution": {"high": 0, "medium": 0, "low": 0}}

    r = result[0]
    return {
        "total": r["total"][0]["count"] if r["total"] else 0,
        "by_source": {item["_id"]: item["count"] for item in r["by_source"] if item["_id"]},
        "by_type": {item["_id"]: item["count"] for item in r["by_type"] if item["_id"]},
        "score_distribution": {
            "high": r["score_high"][0]["count"] if r["score_high"] else 0,
            "medium": r["score_medium"][0]["count"] if r["score_medium"] else 0,
            "low": r["score_low"][0]["count"] if r["score_low"] else 0,
        },
    }


async def delete_findings_by_task(db: AsyncIOMotorDatabase, task_id: str) -> int:
    """删除任务关联的所有 findings"""
    r = await db[FINDINGS_COLLECTION].delete_many({"task_id": task_id})
    return r.deleted_count


async def delete_findings_by_project(db: AsyncIOMotorDatabase, project_id: str) -> int:
    """删除项目的所有 findings"""
    r = await db[FINDINGS_COLLECTION].delete_many({"project_id": project_id})
    return r.deleted_count


async def delete_findings_by_tasks(db: AsyncIOMotorDatabase, task_ids: list[str]) -> int:
    """批量删除多个任务关联的 findings（单次 $in，避免 N+1）"""
    if not task_ids:
        return 0
    r = await db[FINDINGS_COLLECTION].delete_many({"task_id": {"$in": task_ids}})
    return r.deleted_count


# ── Copywriting ──

async def insert_copywriting(db: AsyncIOMotorDatabase, doc: dict[str, Any]) -> None:
    doc.setdefault("created_at", _now())
    await db[COPYWRITINGS_COLLECTION].insert_one(doc)


async def get_copywriting(db: AsyncIOMotorDatabase, finding_id: str) -> dict[str, Any] | None:
    return await db[COPYWRITINGS_COLLECTION].find_one({"finding_id": finding_id}, {"_id": 0})


async def delete_copywritings_by_task(db: AsyncIOMotorDatabase, task_id: str) -> int:
    r = await db[COPYWRITINGS_COLLECTION].delete_many({"task_id": task_id})
    return r.deleted_count


async def delete_copywritings_by_tasks(db: AsyncIOMotorDatabase, task_ids: list[str]) -> int:
    """批量删除多个任务关联的话术（单次 $in，避免 N+1）"""
    if not task_ids:
        return 0
    r = await db[COPYWRITINGS_COLLECTION].delete_many({"task_id": {"$in": task_ids}})
    return r.deleted_count


# ── Profile ──

async def upsert_profile(db: AsyncIOMotorDatabase, finding_id: str, profile: dict[str, Any]) -> None:
    profile["finding_id"] = finding_id
    profile.setdefault("updated_at", _now())
    await db[PROFILES_COLLECTION].update_one(
        {"finding_id": finding_id},
        {"$set": profile, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )


async def get_profile(db: AsyncIOMotorDatabase, finding_id: str) -> dict[str, Any] | None:
    return await db[PROFILES_COLLECTION].find_one({"finding_id": finding_id}, {"_id": 0})
